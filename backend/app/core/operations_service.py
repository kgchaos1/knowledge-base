"""Read-only operations snapshots and disposable SQLite recovery rehearsals.

Never accepts a user path, writes live data, or returns verifier metadata/paths.
"""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile

from app.core import backup_service
from app.core.backup_archive import (
    BackupArchiveError, MAX_ENTRIES, MAX_FILE_SIZE, MAX_MANIFEST_SIZE,
    MAX_TOTAL_SIZE, verify_archive,
)
from app.models.note import DataSource, ImportTask, Note

_IMAGE = re.compile(r"[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)\Z")
BACKUP_ID = re.compile(r"[0-9a-f]{32}\Z")  # Existing service uses uuid.uuid4().hex.
_CHUNK = 1024 * 1024


class OperationsError(Exception):
    """Internal error category; callers must return only fixed public messages."""


def status_snapshot(db):
    # The database volume is what stores live PKM data. Never return its path.
    usage = shutil.disk_usage(backup_service.db_path().resolve().parent)
    references_count = 0
    unique = set()
    # Select only images, not content, task config, cookies, or filesystem entries.
    for (images,) in db.query(Note.images).yield_per(200):
        if not isinstance(images, list):
            continue
        for image in images:
            name = image.get("path") if isinstance(image, dict) else None
            if isinstance(name, str) and _IMAGE.fullmatch(name):
                references_count += 1
                unique.add(name)
    return {
        "disk": {"total": int(usage.total), "used": int(usage.used), "free": int(usage.free)},
        "notes_count": db.query(Note).count(),
        "attachments": {"references_count": references_count, "unique_count": len(unique)},
        "failed_imports_count": db.query(ImportTask).filter(ImportTask.status == "failed").count(),
        "backup": {
            "running": backup_service.STATE.get("running") is True,
            # Never echo a mutable service error (it could contain a path/secret).
            "last_error": "Backup did not complete" if backup_service.STATE.get("last_error") else None,
        },
        "source_scheduler": {
            "enabled_count": db.query(DataSource).filter(DataSource.sync_enabled.is_(True)).count(),
            "active_count": db.query(DataSource).filter(DataSource.active_task_id.is_not(None)).count(),
        },
    }


def _read_only(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA query_only=ON")
    return db


def _sample(db):
    if db.execute("SELECT type FROM sqlite_master WHERE name='notes'").fetchone() != ("table",):
        raise OperationsError()
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise OperationsError()
    return db.execute("SELECT count(*) FROM notes").fetchone()[0]


def rehearse_backup(item):
    """Verify a stable private archive copy, restore SQLite ONLY inside temp dir.

    Attachments are validated by verify_archive, not restored into the live tree.
    A copied archive prevents a replacement between verification and DB sampling.
    """
    root = backup_service.ROOT.resolve()
    name = item.get("filename")
    if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
        raise OperationsError()
    path = root / name
    if path.parent.resolve() != root or path.is_symlink():
        raise OperationsError()
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OperationsError()
    # ZIP overhead is bounded as well as expanded data. No unbounded local copy.
    max_archive = MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE + MAX_ENTRIES * 1024
    if before.st_size > max_archive:
        raise OperationsError()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with tempfile.TemporaryDirectory(prefix="pkm-rehearse-") as directory:
        scratch = Path(directory)
        archive_path = scratch / "archive.zip"
        with os.fdopen(os.open(path, flags), "rb") as source, archive_path.open("xb") as target:
            opened = os.fstat(source.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise OperationsError()
            size = 0
            while chunk := source.read(_CHUNK):
                size += len(chunk)
                if size > max_archive:
                    raise OperationsError()
                target.write(chunk)
            if size != opened.st_size:
                raise OperationsError()
        verified = verify_archive(archive_path)
        # No extract/extractall, user paths, attachment writes, or live DB writes.
        snapshot = scratch / "snapshot.db"
        expected = verified["files"]["pkm.db"]
        digest = hashlib.sha256()
        size = 0
        with zipfile.ZipFile(archive_path) as archive:
            with archive.open("pkm.db") as source, snapshot.open("xb") as target:
                while chunk := source.read(_CHUNK):
                    size += len(chunk)
                    if size > MAX_FILE_SIZE or size > expected["size"]:
                        raise BackupArchiveError("Invalid database size")
                    digest.update(chunk)
                    target.write(chunk)
        if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
            raise OperationsError()
        restored = scratch / "restored.db"
        with closing(_read_only(snapshot)) as source:
            count = _sample(source)
            with closing(sqlite3.connect(restored)) as target:
                target.execute("PRAGMA trusted_schema=OFF")
                source.backup(target)
        with closing(_read_only(restored)) as target:
            restored_count = _sample(target)
        if count != verified["notes_count"] or restored_count != count:
            raise OperationsError()
        return {
            "verified": True,
            "notes_count": count,
            "files_count": verified["files_count"],
            "sqlite_sample_read": True,
            "sqlite_restore_verified": True,
            "live_data_modified": False,
        }
