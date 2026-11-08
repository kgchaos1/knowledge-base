"""Zhihu imports. Network failures are explicit and never include credentials."""

import asyncio
import logging
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models.note import Note
from app.core.config import settings

logger = logging.getLogger(__name__)
PAGE_SIZE = 20
MAX_PAGES = 5
REQUEST_DELAY = 1
# Bounded single-process arbitration, including scheduler-owned worker threads.
# Never held during enrichment/network awaits; not a multi-process DB constraint.
_IDENTITY_LOCKS = tuple(threading.Lock() for _ in range(128))


def _numeric_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value)
    return value if re.fullmatch(r"[0-9]+", value) else None


def _collection_id(value):
    direct = _numeric_id(value)
    if direct:
        return direct
    if not isinstance(value, str):
        raise ValueError("Collection URL or numeric ID is required")
    if re.search(r"\s", value):
        raise ValueError("Collection URL must not contain whitespace")
    try:
        url = urlsplit(value)
        match = re.fullmatch(r"/collection/([0-9]+)/?", url.path)
        if (url.scheme in {"http", "https"}
                and url.netloc in {"www.zhihu.com", "zhihu.com"} and match):
            return match.group(1)
    except ValueError:
        pass
    raise ValueError("Collection must be an ASCII numeric ID or a Zhihu collection URL")


def _plain_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Invalid content field")
    soup = BeautifulSoup(value, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    return soup.get_text("\n", strip=True).strip()


def _url_identity(value):
    """Verified Zhihu URLs identify legacy numeric source IDs and their type."""
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme not in {"http", "https"}:
        return None
    if url.netloc in {"www.zhihu.com", "zhihu.com"}:
        match = re.fullmatch(r"/(?:question/[0-9]+/)?answer/([0-9]+)/?", url.path)
        if match:
            return "answer", match.group(1)
    if url.netloc == "zhuanlan.zhihu.com":
        match = re.fullmatch(r"/p/([0-9]+)/?", url.path)
        if match:
            return "article", match.group(1)
    return None


def _existing_note(db, kind, item_id):
    candidates = db.query(Note).filter(
        Note.source == "zhihu",
        Note.source_id.in_([f"{kind}:{item_id}", item_id]),
    ).all()
    return next((note for note in candidates
                 if note.source_id == f"{kind}:{item_id}"
                 or _url_identity(note.source_url) == (kind, item_id)), None)


def _associate_source(db, note, source_id):
    """Only the task runner's trusted integer ID can establish membership."""
    if source_id is None:
        return
    from app.models_membership import NoteSource
    db.flush()  # New notes need their ID before the membership is inserted.
    if db.get(NoteSource, (source_id, note.id)) is None:
        db.add(NoteSource(source_id=source_id, note_id=note.id))


def _page_data(response):
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        raise RuntimeError("Zhihu returned invalid JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError("Zhihu returned an invalid data format")
    paging = payload.get("paging")
    if not isinstance(paging, dict) or not isinstance(paging.get("is_end"), bool):
        raise RuntimeError("Zhihu returned invalid pagination metadata")
    items = payload["data"]
    if any(not isinstance(item, dict) or not isinstance(item.get("content"), dict)
           for item in items):
        raise RuntimeError("Zhihu returned an invalid item format")
    if not items and not paging["is_end"]:
        raise RuntimeError("Zhihu pagination returned an empty non-final page")
    return items, paging["is_end"]


async def import_from_zhihu(config: dict, db: Session) -> dict:
    """Import at most 100 collection items using asynchronous HTTP.

    Required: collection_url (numeric ID or Zhihu URL), cookie (or settings).
    success counts newly inserted notes; duplicates and skipped are separate.
    Network/schema failures raise sanitized errors, including after partial work.
    Previously committed notes remain and a retry safely deduplicates them.
    """
    source_id = config.get("_source_id")
    if source_id is not None and (type(source_id) is not int or source_id <= 0):
        raise ValueError("_source_id must be a positive integer")
    collection_id = _collection_id(config.get("collection_url"))
    cookie = config.get("cookie") or settings.ZHIHU_COOKIE
    if not isinstance(cookie, str) or not cookie.strip():
        raise ValueError("Zhihu cookie is required; provide cookie or ZHIHU_COOKIE")
    if not cookie.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in cookie):
        raise ValueError("Zhihu cookie contains invalid header characters")
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookie,
        "Referer": f"https://www.zhihu.com/collection/{collection_id}",
    }
    total = success = failed = skipped = duplicates = 0
    updated = incomplete = image_failed = 0
    changed_note_ids = []
    truncated = False
    api_url = f"https://www.zhihu.com/api/v4/collections/{collection_id}/items"
    async with httpx.AsyncClient(headers=headers, timeout=10, follow_redirects=False) as client:
        for page in range(MAX_PAGES):
            try:
                response = await client.get(api_url, params={"limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "include": "data[*].content.content"})
            except httpx.TimeoutException:
                raise RuntimeError("Zhihu request timed out; retry later") from None
            except httpx.RequestError:
                raise RuntimeError("Zhihu request failed due to a network error") from None
            if response.status_code != 200:
                hints = {
                    401: "authentication failed; check the Zhihu cookie",
                    403: "access denied; check collection access or account verification",
                    404: "collection not found",
                    429: "rate limited; retry later",
                }
                hint = hints.get(response.status_code, "upstream request failed")
                raise RuntimeError(f"Zhihu HTTP {response.status_code}: {hint}")
            items, is_end = _page_data(response)
            # Never follow remote paging.next URLs (which could leak the Cookie).
            remaining = MAX_PAGES * PAGE_SIZE - total
            page_items = items[:min(PAGE_SIZE, remaining)]
            oversized = len(items) > len(page_items)
            for item in page_items:
                total += 1
                obj = item["content"]
                kind = obj.get("type")
                if not isinstance(kind, str) or kind not in {"answer", "article"}:
                    skipped += 1
                    continue
                item_id = _numeric_id(obj.get("id"))
                if not item_id:
                    failed += 1
                    continue
                identity_lock = None
                try:
                    fallback = obj.get('content') or ''
                    if not _plain_text(fallback) and '<img' not in fallback.lower():
                        fallback = obj.get('excerpt') or ''
                    if config.get('_enrich', False):
                        from app.importers.zhihu_content import enrich_content
                        enriched = await enrich_content(kind, item_id, fallback, cookie, full_html=obj.get('content'))
                    else:
                        enriched = dict(text=_plain_text(fallback), completeness='excerpt', images=[], image_total=0, image_failed=0, warning='')
                    # Recheck under a process-local stripe after enrichment:
                    # scheduler threads may have committed this identity meanwhile.
                    # Release in finally, with no await while held.
                    identity_lock = _IDENTITY_LOCKS[hash((kind, item_id)) % len(_IDENTITY_LOCKS)]
                    identity_lock.acquire()
                    existing = _existing_note(db, kind, item_id)
                    text = enriched['text']
                    image_failed += enriched['image_failed']
                    if enriched['completeness'] != 'full':
                        incomplete += 1
                    if existing:
                        duplicates += 1
                        # Never replace an existing body with a failed/short fallback.
                        replace_body = bool(text) and (not existing.content or (enriched['completeness'] == 'full' and (existing.content_status == 'full' or len(text) >= len(existing.content))))
                        if replace_body:
                            changed = existing.content != text
                            existing.content = text
                            existing.content_status = enriched['completeness']
                            if changed:
                                existing.ai_stale = 1 if existing.ai_summary else 0
                                existing.ai_processed = 0
                                changed_note_ids.append(existing.id)
                                updated += 1
                        warning = enriched['warning']
                        if enriched['image_failed']:
                            # A partial download is not evidence that old images
                            # were removed upstream. Preserve local associations.
                            merged = {image['path']: image for image in (existing.images or [])
                                      if isinstance(image, dict) and image.get('path')}
                            retained = set(merged) - {image['path'] for image in enriched['images']}
                            merged.update({image['path']: image for image in enriched['images']})
                            existing.images = list(merged.values())
                            if retained:
                                warning = (warning + ' Previous local images retained; image counts describe this fetch.').strip()
                        elif enriched['images']:
                            existing.images = enriched['images']
                        existing.image_total = enriched['image_total'] if enriched['image_failed'] else max(enriched['image_total'], len(existing.images or []))
                        existing.image_failed = enriched['image_failed']
                        existing.content_warning = warning or None
                        if existing.content and not existing.ai_processed and existing.id not in changed_note_ids:
                            changed_note_ids.append(existing.id)
                        _associate_source(db, existing, source_id)
                        db.commit()
                        continue
                    if not text and not enriched['images']:
                        skipped += 1
                        continue
                    question = obj.get("question") or {}
                    author = obj.get("author") or {}
                    if not isinstance(question, dict) or not isinstance(author, dict):
                        raise ValueError("Invalid item metadata")
                    if kind == "answer":
                        title = question.get("title") or "无标题"
                        question_id = _numeric_id(question.get("id"))
                        source_url = (f"https://www.zhihu.com/question/{question_id}/answer/{item_id}"
                                      if question_id else f"https://www.zhihu.com/answer/{item_id}")
                    else:
                        title = obj.get("title") or "无标题"
                        source_url = f"https://zhuanlan.zhihu.com/p/{item_id}"
                    # Retain a supplied public source URL only if its type and ID agree.
                    if _url_identity(obj.get("url")) == (kind, item_id):
                        source_url = obj["url"]
                    name = author.get("name")
                    note = Note(
                        title=str(title)[:200], content=text, source="zhihu",
                        source_id=f"{kind}:{item_id}", source_url=source_url,
                        tags=[name] if isinstance(name, str) and name else [],
                        category="zhihu_collection", ai_processed=0,
                        content_status=enriched['completeness'], images=enriched['images'],
                        image_total=enriched['image_total'], image_failed=enriched['image_failed'],
                        content_warning=enriched['warning'] or None,
                    )
                    db.add(note)
                    _associate_source(db, note, source_id)
                    db.commit()
                    success += 1
                    if text:
                        changed_note_ids.append(note.id)
                except Exception:
                    failed += 1
                    db.rollback()
                    # DB exceptions can embed content and SQL; do not log their text.
                    logger.warning("Failed to import a Zhihu item")
                finally:
                    if identity_lock is not None:
                        identity_lock.release()
            if oversized:
                truncated = True
                break
            if is_end:
                break
            if page == MAX_PAGES - 1:
                truncated = True
                break
            await asyncio.sleep(REQUEST_DELAY)
    status = "partial" if failed or skipped or truncated or (config.get('_enrich') and (incomplete or image_failed)) else "completed"
    message = (f"Imported {success} of {total} processed items; "
               f"duplicates={duplicates}, skipped={skipped}, failed={failed}.")
    if truncated:
        message += f" Truncated: pagination/item limit reached (max {MAX_PAGES} pages, {MAX_PAGES * PAGE_SIZE} items)."
    return dict(total=total, success=success, failed=failed, skipped=skipped,
                duplicates=duplicates, truncated=truncated, status=status, message=message,
                 updated=updated, incomplete=incomplete, image_failed=image_failed, changed_note_ids=changed_note_ids)


async def import_from_zhihu_manual(file_path: str, db: Session) -> dict:
    """Import manually exported text without blocking on file reads."""
    path = Path(file_path)
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"File not found: {file_path}") from None
    items = [item.strip() for item in re.split(r"\n---+\n|\n===+\n", content) if item.strip()]
    success = failed = skipped = 0
    for item in items:
        if len(item) < 10:
            skipped += 1
            continue
        try:
            lines = item.split("\n", 1)
            text = lines[1].strip() if len(lines) > 1 else item
            if not text:
                skipped += 1
                continue
            note = Note(title=lines[0][:100], content=text, source="zhihu",
                        source_id=None, source_url=None, tags=[], category=None, ai_processed=0)
            db.add(note)
            db.commit()
            success += 1
        except Exception:
            failed += 1
            db.rollback()
            logger.warning("Failed to import a manual Zhihu item")
    return dict(total=len(items), success=success, failed=failed, skipped=skipped)
