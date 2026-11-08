"""Read-only full-index synchronization, without revision tables or DB writes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.note import DataSource, Note
from app.models.schemas import NoteResponse
from app.models_membership import NoteSource

MAX_NOTES = 100_000
MAX_ATTACHMENTS = 100_000
_IMAGE = re.compile(r"[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)\Z")


class SyncLimitError(ValueError):
    pass


def canonical_json(value) -> bytes:
    """UTF-8, recursively sorted object keys, no whitespace, no NaN/Infinity.

    Array order (including images and tags) is meaningful and is preserved.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def revision(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@contextmanager
def read_session(db: Session):
    """Use the injected Session's existing engine, never its pending changes.

    Python sqlite3 legacy transaction mode does not BEGIN on SELECT. Explicitly
    BEGIN before the first read; SQLAlchemy autobegin alone is not sufficient.
    A separate Session also avoids autoflush/identity-map stale data. Only SELECT
    and transaction control are executed; closing rolls back the read transaction.
    """
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as reader:
        connection = reader.connection()
        if connection.dialect.name == "sqlite":
            raw = connection.connection.driver_connection
            if not raw.in_transaction:
                connection.exec_driver_sql("BEGIN")
        try:
            yield reader
        finally:
            reader.rollback()


def attachment_entry(root: Path, filename: str) -> dict:
    """Missing, symlinked, unreadable or hash-invalid images are unavailable."""
    expected = filename.split(".", 1)[0]
    entry = {"filename": filename, "sha256": expected, "size": None,
             "available": False, "url": "/api/attachments/" + filename}
    path = root / filename
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or path.is_symlink():
            return entry
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return entry
            digest = hashlib.sha256()
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(source.fileno())
        if (size != opened.st_size or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns or digest.hexdigest() != expected):
            return entry
        entry.update(size=size, available=True)
    except OSError:
        pass
    return entry


def normalize_scope(mode=None, source_ids=None) -> dict:
    """None means all; an explicitly supplied empty source list means no notes."""
    if mode not in (None, "all", "sources"):
        raise ValueError("scope must be all or sources")
    if source_ids is not None:
        if not isinstance(source_ids, list) or len(source_ids) > 1000:
            raise ValueError("source_ids must contain at most 1000 IDs")
        if any(type(value) is not int or value <= 0 for value in source_ids):
            raise ValueError("source_ids must be positive strict integers")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_ids must be unique")
        if mode == "all":
            raise ValueError("source_ids cannot accompany scope=all")
    mode = mode or ("sources" if source_ids is not None else "all")
    return {"mode": mode, "source_ids": sorted(source_ids or [])}


def load_snapshot(reader: Session, attachment_root: Path, *, scope=None,
                  source_ids=None) -> tuple[dict, dict[int, dict]]:
    """Materialize public fields within an explicit, snapshot-bound scope."""
    selected_scope = normalize_scope(scope, source_ids)
    query = select(Note.__table__)
    if selected_scope["mode"] == "sources":
        # EXISTS avoids duplicate notes when multiple selected sources overlap.
        query = query.where(select(NoteSource.note_id).join(
            DataSource, DataSource.id == NoteSource.source_id,
        ).where(
            NoteSource.note_id == Note.id,
            NoteSource.source_id.in_([value for value in selected_scope["source_ids"]
                                     if value <= 2**63 - 1]),
        ).exists())
    rows = reader.execute(query.order_by(Note.id).limit(MAX_NOTES + 1)).mappings().all()
    if len(rows) > MAX_NOTES:
        raise SyncLimitError("Sync full index exceeds 100000 notes")
    notes = {}
    index = []
    filenames = set()
    for row in rows:
        # Explicit public schema: no ImportTask, settings, Cookie or source secrets.
        payload = NoteResponse.model_validate(dict(row)).model_dump(mode="json")
        value = revision(payload)
        notes[payload["id"]] = dict(payload, revision=value)
        index.append({"id": payload["id"], "revision": value})
        for image in payload["images"]:
            filename = image.get("path") if isinstance(image, dict) else None
            if isinstance(filename, str) and _IMAGE.fullmatch(filename):
                filenames.add(filename)
                if len(filenames) > MAX_ATTACHMENTS:
                    raise SyncLimitError("Sync full index exceeds 100000 attachments")
    attachments = [attachment_entry(attachment_root, name) for name in sorted(filenames)]
    manifest = {"protocol": 1, "notes": index, "attachments": attachments}
    # Preserve pre-scope all snapshot hashes for existing clients. Filtered
    # snapshots include scope even when their note sets happen to be identical.
    if selected_scope["mode"] == "sources":
        manifest["scope"] = selected_scope
    manifest["snapshot"] = revision(manifest)
    manifest["scope"] = selected_scope
    return manifest, notes
