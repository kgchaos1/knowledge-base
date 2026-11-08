"""Pure, dependency-free rendering of API note dictionaries into Obsidian Markdown.

Untrusted Markdown keeps headings/lists/emphasis but not active links, embeds,
raw HTML, or executable fenced-code languages. Only validated content-addressed
image records can create embeds. Rendering never reads files or uses the network.
"""
from datetime import date, datetime
import html
import json
import re
import unicodedata
from urllib.parse import quote, urlsplit


_IMAGE_NAME = re.compile(r"[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)\Z")
_IMAGE_MARKUP = re.compile(r"!\s*\[|<\s*(?:img|picture|source)\b", re.I)
_SECTION = re.compile(r"^\s*【([^\r\n【】]+)】\s*$", re.M)


def stable_path(note: dict) -> str:
    """Return an ID-only vault-relative path; bools/strings are not IDs."""
    note_id = note.get("id")
    if type(note_id) is not int or note_id <= 0:
        raise ValueError("note.id must be a strictly positive integer")
    return f"笔记/pkm-{note_id}.md"


def _text(value, default=""):
    return value if isinstance(value, str) else default


def _single_line(value):
    return " ".join("".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in _text(value)
    ).split())


def _literal(value):
    """Escape Markdown punctuation as entities (also safe in HTML text)."""
    text = html.escape(value, quote=False)
    return re.sub(r"[\\`*_{}\[\]()#!|~]", lambda m: f"&#{ord(m[0])};", text)


def _tags(value):
    result = []
    for tag in value if isinstance(value, list) else []:
        if not isinstance(tag, str):
            continue
        tag = re.sub(r"\s+", "-", tag.strip())
        segments = tag.split("/")
        if (not tag or any(not part for part in segments)
                or not all(char.isalnum() or char in "_-/" for char in tag)
                or not any(char.isalpha() or char in "_-" for char in tag)):
            continue
        if tag not in result:
            result.append(tag)
    return result


def _source_link(value):
    """Validate before parsing, as urlsplit silently removes some controls."""
    if not isinstance(value, str) or not value:
        return None
    if any(char.isspace() or unicodedata.category(char).startswith("C")
           for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        # Accessing port validates invalid ports, even though it is not changed.
        parsed.port
    except ValueError:
        return None
    # Angle brackets bound an autolink; encode all syntax that could break out.
    return quote(value, safe=":/?#@!$&'*+,;=%~-._[]")


def _markdown(value):
    """Preserve passive Markdown and make active syntax visibly literal.

    Brackets are entities rather than backslash escapes to avoid an attacker
    cancelling an escape with a preceding backslash. Existing entities are
    escaped first. All incoming image syntax is inert, including references.
    """
    text = _text(value).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    warning = bool(_IMAGE_MARKUP.search(text))
    # Normalize every fence to a passive language, including nested quote/list
    # fences. A line-based prefix keeps Markdown structure without trusting info.
    output = []
    fence_char = None
    fence_size = 0
    for line in text.split("\n"):
        # Match fences even inside arbitrarily indented lists/quotes. Plugins
        # must not receive an executable language through a nested code block.
        fence = re.search(r"(`{3,}|~{3,})([^`~]*)$", line)
        if fence:
            prefix = line[:fence.start()]
            marker, info = fence.groups()
            if fence_char is None:
                fence_char, fence_size = marker[0], len(marker)
                line = prefix + marker + "text"
            elif marker[0] == fence_char and len(marker) >= fence_size and not info.strip():
                fence_char = None
                line = prefix + marker
            else:
                line = prefix + marker + "text"
        # Raw HTML, autolinks and Markdown/wiki links become inert text. Inline
        # Dataview's `=expr` / `$=expr` forms are neutralized as well.
        line = html.escape(line, quote=False).replace("\\", "&#92;")
        line = line.replace("[", "&#91;").replace("]", "&#93;")
        line = re.sub(r"(`+)(\s*\$?=)", lambda m: m[1] + m[2].replace("=", "&#61;"), line)
        output.append(line)
    # Close unbalanced user fences so subsequent generated content stays outside.
    if fence_char is not None:
        output.append(fence_char * fence_size)
    return "\n".join(output), warning


def render_note(note: dict) -> str:
    """Return Markdown only. Does not mutate ``note`` or perform any I/O."""
    stable_path(note)  # Apply the same strict ID validation at either entry point.
    title = _text(note.get("title"))
    updated = note.get("updated_at")
    if isinstance(updated, (datetime, date)):
        updated = updated.isoformat()
    elif updated is not None and not isinstance(updated, str):
        updated = None
    metadata = {
        "pkm_id": note["id"],
        "source": _text(note.get("source")),
        "source_url": _text(note.get("source_url")),
        "aliases": [title],
        "tags": _tags(note.get("tags")),
        "content_status": _text(note.get("content_status"), "unknown"),
        "ai_stale": note.get("ai_stale") is True or type(note.get("ai_stale")) is int and note["ai_stale"] == 1,
        "updated_at": updated,
    }
    # Keep non-BMP characters literal: JSON surrogate-pair escapes do not have
    # the same meaning in every YAML parser. Escape YAML's extra line separators.
    def scalar(value):
        result = json.dumps(value, ensure_ascii=False)
        for char in ("\u0085", "\u2028", "\u2029"):
            result = result.replace(char, "\\u%04x" % ord(char))
        return result

    lines = ["---"] + [f"{key}: {scalar(value)}"
                           for key, value in metadata.items()] + ["---", ""]
    lines += ["# " + _literal(_single_line(title) or "无标题"), "", "## AI 摘要", ""]
    summary = _text(note.get("ai_summary"))
    summary = _SECTION.sub(lambda m: "### " + m[1].strip(), summary)
    summary, summary_images = _markdown(summary if summary.strip() else "暂无摘要。")
    lines += [summary, ""]
    if metadata["ai_stale"]:
        lines += ["> 摘要可能已过期，请重新生成。", ""]
    url = _source_link(note.get("source_url"))
    if url:
        lines += [f"原文链接：<{url}>", ""]
    elif note.get("source_url"):
        lines += ["> 警告：不安全的原文链接已禁用。", ""]

    content = _text(note.get("content"))
    body, body_images = _markdown(content if content.strip() else "暂无正文。")
    local_images, rejected_images = [], False
    for image in note.get("images") if isinstance(note.get("images"), list) else []:
        filename = image.get("path") if isinstance(image, dict) else None
        if isinstance(filename, str) and _IMAGE_NAME.fullmatch(filename):
            if filename not in local_images:
                local_images.append(filename)
        else:
            rejected_images = True
    if summary_images or body_images or rejected_images:
        lines += ["> 警告：远程图片或不安全嵌入已禁用；仅展示本地哈希附件，不会加载远程图片。", ""]
    content_warning = _text(note.get("content_warning"))
    if content_warning.strip():
        lines += ["> 内容提示：" + _literal(_single_line(content_warning)), ""]
    lines += ["> [!quote]- 原文与图片"]
    lines += ["> " + line for line in body.split("\n")]
    if local_images:
        lines += [">", "> ### 本地图片"]
        lines += [f"> ![[附件/{filename}]]" for filename in local_images]
    return "\n".join(lines) + "\n"
