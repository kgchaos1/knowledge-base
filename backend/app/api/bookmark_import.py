"""Inert Netscape bookmark imports: parse links only, never render or fetch HTML."""
import hashlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.note import Note

router = APIRouter(prefix="/bookmark-import", tags=["bookmark-import"])
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_LINKS = 1000
MAX_URL_LENGTH = 2000
_READ_CHUNK = 64 * 1024


class BookmarkItem(BaseModel):
    title: str
    url: str
    folder: str = ""


class PreviewResponse(BaseModel):
    filename: str
    total: int
    valid: int
    skipped: int
    duplicates_in_file: int
    items: list[BookmarkItem]
    content_hash: str


class CommitResponse(BaseModel):
    imported: int
    duplicates: int
    skipped: int
    total: int
    note_ids: list[int]


@dataclass(frozen=True)
class BookmarkUpload:
    filename: str
    total: int
    skipped: int
    duplicates_in_file: int
    items: list[BookmarkItem]
    content_hash: str


def canonicalize_url(value: str) -> str | None:
    """Validate without DNS/network and normalize only scheme/host/port/fragment."""
    if not value or len(value) > MAX_URL_LENGTH:
        return None
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
        return None
    if "\\" in value or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", value, re.I):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or "@" in parts.netloc:
            return None
        host = parts.hostname
        port = parts.port  # Validates the numeric range, including malformed ports.
        if not host or parts.netloc.endswith(":"):
            return None
        if parts.netloc.startswith("[") and not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", parts.netloc):
            return None
        host = host.rstrip(".").lower()
        if not host or host == "localhost" or host.endswith(".localhost") or "%" in host:
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            host = host.encode("idna").decode("ascii").lower()
            labels = host.split(".")
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                return None
            # Reject legacy numeric IPv4 spellings (127.1, 2130706433,
            # 0177.0.0.1, 0x7f000001) rather than interpreting them as DNS.
            if all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in labels):
                return None
            if host == "localhost" or host.endswith(".localhost"):
                return None
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                return None
            mapped = getattr(address, "ipv4_mapped", None)
            if mapped is not None and (not mapped.is_global or mapped.is_multicast or mapped.is_reserved):
                return None
            host = address.compressed
            if address.version == 6:
                host = "[" + host + "]"
        scheme = parts.scheme.lower()
        authority = host
        if port is not None and not (scheme == "http" and port == 80 or scheme == "https" and port == 443):
            authority += ":" + str(port)
        result = urlunsplit((scheme, authority, parts.path, parts.query, ""))
        return result if len(result) <= MAX_URL_LENGTH else None
    except (ValueError, UnicodeError):
        return None


async def _read_bookmarks(file: UploadFile) -> BookmarkUpload:
    try:
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or any(unicodedata.category(char).startswith("C") for char in filename):
            raise HTTPException(422, "A valid HTML filename is required")
        if filename.rsplit(".", 1)[-1].lower() not in {"html", "htm"} or "." not in filename:
            raise HTTPException(415, "Only .html and .htm bookmark files are supported")
        data = bytearray()
        while True:
            chunk = await file.read(min(_READ_CHUNK, MAX_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(413, "File exceeds the 2 MiB limit")
        try:
            content = data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise HTTPException(422, "File must contain valid UTF-8 text") from None
        if not content.strip() or "\x00" in content:
            raise HTTPException(422, "A non-empty HTML bookmark file is required")
        # The built-in parser does not load external DTDs, images or scripts.
        soup = BeautifulSoup(content, "html.parser")
        if soup.find(["html", "dl", "a"]) is None and "<!doctype netscape-bookmark-file-1" not in content.lower():
            raise HTTPException(422, "File must contain bookmark HTML")
        links = soup.find_all("a", href=True, limit=MAX_LINKS + 1)
        if len(links) > MAX_LINKS:
            raise HTTPException(413, "File exceeds the 1000 link limit")
        items = []
        seen = set()
        skipped = duplicates = 0
        for link in links:
            url = canonicalize_url(link.get("href", ""))
            if url is None:
                skipped += 1
                continue
            if url in seen:
                duplicates += 1
                continue
            seen.add(url)
            title = link.get_text(" ", strip=True)[:500] or url[:500]
            # Folder extraction is intentionally omitted: malformed nested DLs
            # must not accidentally attribute a link to an unrelated heading.
            items.append(BookmarkItem(title=title, url=url, folder=""))
        return BookmarkUpload(filename, len(links), skipped, duplicates, items,
                              hashlib.sha256(data).hexdigest())
    finally:
        await file.close()


@router.post("/preview", response_model=PreviewResponse)
async def preview_bookmarks(file: UploadFile = File(...)):
    uploaded = await _read_bookmarks(file)
    return PreviewResponse(filename=uploaded.filename, total=uploaded.total,
                           valid=len(uploaded.items), skipped=uploaded.skipped,
                           duplicates_in_file=uploaded.duplicates_in_file,
                           items=uploaded.items[:20], content_hash=uploaded.content_hash)


@router.post("/commit", response_model=CommitResponse)
async def commit_bookmarks(file: UploadFile = File(...), content_hash: str | None = Form(None),
                           db: Session = Depends(get_db)):
    uploaded = await _read_bookmarks(file)
    if content_hash is not None and content_hash != uploaded.content_hash:
        raise HTTPException(422, "File does not match the preview; preview the file again")
    # From the first DB read through commit there is deliberately no await.
    # This serializes competing imports on this application's single worker;
    # it is not a claim of cross-worker uniqueness without a database constraint.
    try:
        hashes = {hashlib.sha256(item.url.encode("utf-8")).hexdigest(): item for item in uploaded.items}
        # Chunk IN clauses for SQLite builds with the older 999-variable limit.
        existing = set()
        keys = list(hashes)
        for start in range(0, len(keys), 500):
            existing.update(row[0] for row in db.query(Note.source_id).filter(
                Note.source == "bookmark", Note.source_id.in_(keys[start:start + 500])).all())
        notes = []
        for digest, item in hashes.items():
            if digest in existing:
                continue
            note = Note(title=item.title, content=item.url, source="bookmark",
                        source_id=digest, source_url=item.url, content_status="link",
                        ai_processed=0, category=item.folder or None,
                        images=[], image_total=0, image_failed=0)
            db.add(note)
            notes.append(note)
        db.flush()
        result = CommitResponse(imported=len(notes), duplicates=uploaded.duplicates_in_file + len(existing),
                                skipped=uploaded.skipped, total=uploaded.total,
                                note_ids=[note.id for note in notes])
        db.commit()
        return result
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(500, "Unable to import bookmarks") from None
