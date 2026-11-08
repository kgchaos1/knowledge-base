"""Isolated, best-effort Zhihu detail/image enrichment (no database side effects).

DNS answers are checked immediately before each image request, but httpx resolves
again when connecting: this is NOT DNS pinning and has a DNS-rebinding TOCTOU
boundary. Deploy with outbound private-network denial for a hard SSRF boundary.
Injected clients/resolvers are trusted infrastructure, not untrusted input.
"""
import asyncio
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

MAX_IMAGES = 20
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_DETAIL_BYTES = 4 * 1024 * 1024
REQUEST_SECONDS = 15
ATTACHMENT_DIR = Path('/app/data/attachments')


def _image_host(url):
    if not isinstance(url, str) or re.search(r'[\s\\]', url):
        raise ValueError('Unsafe image URL')
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username is not None
            or parsed.password is not None or parsed.netloc.lower() != host
            or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+zhimg\.com', host)
            or parsed.fragment):
        raise ValueError('Unsafe image URL')
    return host


def _extract(html):
    soup = BeautifulSoup(html if isinstance(html, str) else '', 'html.parser')
    for node in soup(['script', 'style', 'noscript', 'template']):
        node.decompose()
    images = []
    for img in soup.find_all('img'):
        url = next((img.get(key) for key in ('data-original', 'data-actualsrc', 'src')
                    if isinstance(img.get(key), str) and img.get(key).strip()), '')
        images.append({'url': url, 'alt': str(img.get('alt') or '')})
    return soup.get_text('\n', strip=True).strip(), images


async def _resolve(host):
    records = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM)
    return [record[4][0] for record in records]


async def _check_dns(host, resolver):
    addresses = await resolver(host)
    if not addresses:
        raise ValueError('No public image address')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        # Reject mixed public/private answers as well as IPv4-mapped private IPv6.
        mapped = getattr(ip, 'ipv4_mapped', None)
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or (mapped is not None and (not mapped.is_global or mapped.is_multicast or mapped.is_reserved))):
            raise ValueError('Non-public image address')


def _extension(data):
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    raise ValueError('Unsupported image magic')


async def _body(client, url, limit, budget=None, cookie=None):
    # A raw Request bypasses client cookies/default headers. auth=None bypasses
    # client auth. An injected client's event hooks/transport must still be trusted.
    headers = {'Accept-Encoding': 'identity'}
    if cookie:
        headers['Cookie'] = cookie
    request = httpx.Request('GET', url, headers=headers,
                            extensions={'timeout': dict(connect=5, read=5, write=5, pool=5)})
    response = await client.send(request, stream=True, auth=None, follow_redirects=False)
    try:
        if response.status_code != 200:
            raise ValueError('Upstream non-success status')
        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
            raise ValueError('Encoded response rejected')
        length = response.headers.get('content-length')
        if length is not None and (int(length) < 0 or int(length) > limit
                                   or (budget is not None and int(length) > budget[0])):
            raise ValueError('Response exceeds byte limit')
        chunks = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if budget is not None:
                budget[0] -= len(chunk)
            if len(chunks) + len(chunk) > limit or (budget is not None and budget[0] < 0):
                raise ValueError('Response exceeds byte limit')
            chunks.extend(chunk)
        return bytes(chunks)
    finally:
        await response.aclose()


def _save(data, directory):
    extension = _extension(data)
    digest = hashlib.sha256(data).hexdigest()
    name = f'{digest}.{extension}'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    fd, temporary = tempfile.mkstemp(prefix='.zhihu-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Same-directory hard link atomically publishes a complete file and
            # never replaces an existing content-addressed destination.
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Attachment digest conflict')
    finally:
        os.unlink(temporary)
    return name


async def enrich_content(kind, id, fallback_html, cookie, *, client=None,
                         attachment_dir=ATTACHMENT_DIR, resolver=None, full_html=None):
    """Return {text, completeness, images, image_total, image_failed, warning}.

    kind is answer/article; id is a non-negative ASCII decimal str/int (not bool).
    Invalid kind/id/cookie raises ValueError before any I/O. Cookie may be empty
    or None; otherwise it must be an ASCII header value without control chars.
    Keyword client is an httpx.AsyncClient, borrowed and never closed here;
    resolver is async (hostname) -> iterable of IP strings (for offline tests).

    Only nonempty detail content (visible text or an allowlisted image URL)
    yields full. Failure uses fallback as excerpt if it has text/img, else link.
    Images are downloaded in document order, at most 20 img occurrences, with
    no deduplication of occurrences. image_total counts ALL selected HTML img
    tags; image_failed counts every unsuccessful/skipped occurrence, including
    over-limit tags. images includes only successful {path,url,alt} records;
    path is <sha256>.<jpg|png|gif|webp> (filename only), relative to attachment_dir,
    including when attachment_dir is overridden. Byte budgets count downloaded
    payload, including failed magic checks; 8 MiB/image and 32 MiB/article.
    Warning is an empty string on success, otherwise sanitized summary text.
    Signature checks identify formats, not complete image decoder validation.
    """
    if (not isinstance(kind, str) or kind not in {'answer', 'article'}
            or isinstance(id, bool) or not isinstance(id, (str, int))
            or not re.fullmatch(r'[0-9]+', str(id))):
        raise ValueError('Invalid Zhihu content identity')
    if cookie is not None and (not isinstance(cookie, str) or not cookie.isascii()
                              or any(ord(c) < 32 or ord(c) == 127 for c in cookie)):
        raise ValueError('Invalid Zhihu cookie header')
    if client is None:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as owned:
            return await enrich_content(kind, id, fallback_html, cookie, client=owned,
                                        attachment_dir=attachment_dir, resolver=resolver, full_html=full_html)
    text, candidates = _extract(fallback_html)
    completeness = 'excerpt' if text or candidates else 'link'
    warnings = []
    try:
        if isinstance(full_html, str) and full_html.strip():
            # The collection API's explicitly requested content field is not excerpt.
            content = full_html
        else:
            raw = await asyncio.wait_for(_body(client,
                f'https://www.zhihu.com/api/v4/{kind}s/{id}', MAX_DETAIL_BYTES,
                cookie=cookie), REQUEST_SECONDS)
            import json
            payload = json.loads(raw)
            content = payload.get('content') if isinstance(payload, dict) else None
        if not isinstance(content, str):
            raise ValueError('Missing detail content')
        detail_text, detail_images = _extract(content)
        valid_image = False
        for image in detail_images:
            try:
                _image_host(image['url'])
                valid_image = True
                break
            except ValueError:
                pass
        if not detail_text and not valid_image:
            raise ValueError('Empty detail content')
        text, candidates, completeness = detail_text, detail_images, 'full'
    except (httpx.HTTPError, ValueError, UnicodeError, OSError, asyncio.TimeoutError):
        warnings.append('Detail unavailable; retained fallback.')
    images = []
    budget = [MAX_TOTAL_BYTES]
    async def download(image):
        host = _image_host(image['url'])
        await _check_dns(host, resolver or _resolve)
        return await _body(client, image['url'], MAX_IMAGE_BYTES, budget)
    for image in candidates[:MAX_IMAGES]:
        if budget[0] <= 0:
            break
        try:
            data = await asyncio.wait_for(download(image), REQUEST_SECONDS)
            path = await asyncio.to_thread(_save, data, Path(attachment_dir))
            images.append(dict(path=path, **image))
        except (httpx.HTTPError, ValueError, UnicodeError, OSError, asyncio.TimeoutError):
            continue
    failed = len(candidates) - len(images)
    if failed:
        warnings.append(f'{failed} of {len(candidates)} images unavailable or skipped by limits.')
    return dict(text=text, completeness=completeness, images=images,
                image_total=len(candidates), image_failed=failed, warning=' '.join(warnings))
