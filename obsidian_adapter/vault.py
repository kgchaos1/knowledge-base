"""Conservative, offline Obsidian snapshot writer (never a two-way sync).

Only use a dedicated PKM export/test directory, never an existing personal vault.
The marker stores generated hashes, revisions and paths, not configuration/auth.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Mapping
import zipfile

from .render import render_note, stable_path

_MARKER = ".pkm-export/index.json"
_ATTACHMENT = re.compile(r"[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)\Z")
_NOTE = re.compile(r"笔记/pkm-[A-Za-z0-9_-]+\.md\Z")
_CONFLICT = re.compile(r"冲突/pkm-[A-Za-z0-9_-]+-[0-9a-f]{64}\.md\Z")
_README = "说明.md"
_README_CONTENT = ("# PKM 测试导出\n\n此目录仅用于专用 PKM 导出测试。不是双向同步，"
                   "请勿把真实 Obsidian vault 作为目标。\n"
                   "本地编辑不会被覆盖；服务器删除的笔记会保留；新版本冲突副本位于“冲突”。\n"
                   "导出不包含登录凭据、插件或 Obsidian 配置。\n")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _linked(path: Path) -> bool:
    if path.is_symlink():
        return True
    # Windows junctions/reparse points are also unsuitable export targets.
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) &
                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def _safe(root: Path, relative: str) -> Path:
    part = PurePosixPath(relative)
    if (not relative or "\\" in relative or ":" in relative or part.is_absolute()
            or any(p in ("", ".", "..") for p in relative.split("/"))):
        raise ValueError("Unsafe export path")
    target = root.joinpath(*part.parts)
    if _linked(root):
        raise ValueError("Symlinks/reparse points are forbidden")
    current = root
    for component in part.parts:
        current = current / component
        if _linked(current):
            raise ValueError("Symlinks/reparse points are forbidden")
    return target


def _root(root) -> Path:
    path = Path(os.path.abspath(os.fspath(root)))
    if path == Path(path.anchor) or path == Path.home().absolute():
        raise ValueError("Use a dedicated PKM test/export directory")
    for ancestor in (path, *path.parents):
        if _linked(ancestor):
            raise ValueError("Symlinks/reparse points are forbidden")
    if path.exists() and not path.is_dir():
        raise ValueError("Export root must be a directory")
    return path


def _load(root: Path) -> dict:
    marker = _safe(root, _MARKER)
    if not marker.exists():
        if root.exists() and any(root.iterdir()):
            raise ValueError("Nonempty directory has no managed PKM marker")
        return {"notes": {}, "snapshot": None}
    index = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(index, dict) or set(index) != {"notes", "snapshot"}:
        raise ValueError("Invalid PKM marker")
    if not isinstance(index["notes"], dict) or not isinstance(index["snapshot"], (str, type(None))):
        raise ValueError("Invalid PKM marker")
    for key, entry in index["notes"].items():
        if (not isinstance(entry, dict)
                or set(entry) - {"path", "hash", "revision", "snapshot", "retained"}
                or not {"path", "hash", "revision", "snapshot"} <= set(entry)):
            raise ValueError("Invalid managed note record")
        relative, digest = entry["path"], entry["hash"]
        if (not isinstance(relative, str) or not _NOTE.fullmatch(relative)
                or relative != f"笔记/pkm-{key}.md"):
            raise ValueError("Invalid managed note path")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid managed note hash")
        if (not isinstance(entry["snapshot"], str)
                or entry["revision"] is not None and not isinstance(entry["revision"], (str, int, float, bool))
                or "retained" in entry and not isinstance(entry["retained"], bool)):
            raise ValueError("Invalid managed note metadata")
        _safe(root, relative)
    return index


def _atomic(root: Path, relative: str, data: bytes, *, replace: bool = True) -> None:
    target = _safe(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the same filesystem; replace is atomic, link gives no-clobber.
    fd, temporary = tempfile.mkstemp(prefix=".pkm-stage-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(root, relative)
        if replace:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != data:
                    raise ValueError("Refusing to overwrite existing export file")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _index_bytes(index: dict) -> bytes:
    return (json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _attachment(name: str, data: bytes) -> None:
    if not isinstance(name, str) or not _ATTACHMENT.fullmatch(name):
        raise ValueError("Attachment name must be a lowercase SHA-256 image filename")
    if not isinstance(data, bytes) or _hash(data) != name.split(".")[0]:
        raise ValueError("Attachment SHA-256 mismatch")
    ext = name.rsplit(".", 1)[1]
    valid = ((ext in ("jpg", "jpeg") and data.startswith(b"\xff\xd8\xff")) or
             (ext == "png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
             (ext == "gif" and data.startswith((b"GIF87a", b"GIF89a"))) or
             (ext == "webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
    if not valid:
        raise ValueError("Attachment image signature mismatch")


def apply_snapshot(root, notes, attachments: Mapping[str, bytes], snapshot: str) -> dict:
    """Apply a fully fetched snapshot; return five lists of note IDs.

    Local changes are retained, including when the server has not changed.
    In that case ``retained`` reports the ID. When both changed, ``conflicts``
    reports the ID and a deterministic no-clobber server copy is written.
    Failed writes leave the previous index intact. A new empty root receives
    an empty bootstrap marker, so a retry can reclaim already-generated files.
    """
    if not isinstance(snapshot, str):
        raise ValueError("snapshot must be a string")
    root = _root(root)
    index = _load(root)
    result = {key: [] for key in ("imported", "updated", "unchanged", "conflicts", "retained")}
    staged = []
    seen = set()
    for note in notes:
        note_id = str(note["id"])
        if note_id in seen:
            raise ValueError("Duplicate note id")
        seen.add(note_id)
        relative = str(stable_path(note)).replace("\\", "/")
        if not _NOTE.fullmatch(relative):
            raise ValueError("Invalid stable note path")
        _safe(root, relative)
        generated = render_note(note)
        data = generated.encode("utf-8") if isinstance(generated, str) else generated
        if not isinstance(data, bytes):
            raise ValueError("render_note must return str or bytes")
        revision = note.get("revision", note.get("rev"))
        if revision is not None and not isinstance(revision, (str, int, float, bool)):
            raise ValueError("Invalid note revision")
        staged.append((note_id, relative, data, _hash(data), revision))
    for name, data in attachments.items():
        _attachment(name, data)
        target = _safe(root, "附件/" + name)
        if target.exists() and _hash(target.read_bytes()) != name.split(".")[0]:
            raise ValueError("Existing attachment hash mismatch; refusing overwrite")
    _safe(root, _README)
    # All rendering/input validation precedes any output. Bootstrap is deliberately
    # not a completed snapshot; it allows recovery after an interrupted first run.
    root.mkdir(parents=True, exist_ok=True)
    if not _safe(root, _MARKER).exists():
        _atomic(root, _MARKER, _index_bytes(index), replace=False)
    new_index = {"notes": {k: dict(v) for k, v in index["notes"].items()}, "snapshot": snapshot}
    for name, data in attachments.items():
        _atomic(root, "附件/" + name, data, replace=False)
    for note_id, relative, data, digest, revision in staged:
        target = _safe(root, relative)
        old = index["notes"].get(note_id)
        local = _hash(target.read_bytes()) if target.exists() else None
        entry = {"path": relative, "hash": digest, "revision": revision, "snapshot": snapshot}
        if local == digest:
            result["unchanged"].append(note_id)
            new_index["notes"][note_id] = entry
        elif local is None or (old and old["path"] == relative and local == old["hash"]):
            _atomic(root, relative, data, replace=local is not None)
            result["updated" if old else "imported"].append(note_id)
            new_index["notes"][note_id] = entry
        elif old and digest == old["hash"]:
            result["retained"].append(note_id)
            new_index["notes"][note_id]["retained"] = True
        else:
            filename = PurePosixPath(relative).stem
            _atomic(root, f"冲突/{filename}-{digest}.md", data, replace=False)
            result["conflicts"].append(note_id)
            # Never promote the locally edited original's baseline on conflict.
            if old:
                new_index["notes"][note_id]["retained"] = True
    for note_id in index["notes"]:
        if note_id not in seen:
            result["retained"].append(note_id)
            new_index["notes"][note_id]["retained"] = True
    _atomic(root, _README, _README_CONTENT.encode("utf-8"), replace=False)
    _atomic(root, _MARKER, _index_bytes(new_index))
    return result


def build_zip(root) -> bytes:
    """Package only managed notes, image attachments, conflicts, README and marker.

    Never recursively archive the vault: .obsidian/plugins and arbitrary user
    files cannot enter this allowlist. No network access occurs.
    """
    root = _root(root)
    index = _load(root)
    if not _safe(root, _MARKER).is_file():
        raise ValueError("No managed snapshot to package")
    paths = {_MARKER}
    if _safe(root, _README).is_file():
        paths.add(_README)
    for entry in index["notes"].values():
        if _safe(root, entry["path"]).is_file():
            paths.add(entry["path"])
    for directory, pattern in (("附件", _ATTACHMENT), ("冲突", _CONFLICT)):
        folder = _safe(root, directory)
        if folder.exists():
            for file in folder.iterdir():
                relative = directory + "/" + file.name
                if (pattern.fullmatch(file.name if directory == "附件" else relative)
                        and _safe(root, relative).is_file()):
                    if directory == "附件":
                        _attachment(file.name, file.read_bytes())
                    paths.add(relative)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(paths):
            archive.writestr(relative, _safe(root, relative).read_bytes())
    return output.getvalue()
