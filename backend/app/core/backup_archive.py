"""Offline, standard-library-only PKM archives. Never restores a live database.

Caller MUST hold its attachment read lock throughout create_archive; deletion and
replacement must take the matching write lock. SQLite itself supplies the online
snapshot transaction. Only the DB and referenced images are included, never env,
password files, Cookie files, or unrelated files. Legacy import_tasks.config
credential keys are recursively redacted in the snapshot only (secure_delete and
VACUUM remove old payload pages). Arbitrary note text is not semantically redacted.

Manifest files maps archive names to {sha256, size}; it excludes manifest.json
itself. schema_version is SQLite PRAGMA user_version, format_version is ours.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile
import zlib

MAX_TOTAL_SIZE = 2 * 1024**3
MAX_FILE_SIZE = 512 * 1024**2
MAX_ENTRIES = 50_000
MAX_MANIFEST_SIZE = 16 * 1024**2
_CHUNK = 1024 * 1024
_IMAGE = re.compile(r"[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class BackupArchiveError(ValueError):
    """Invalid archive/source or an incomplete backup; no archive is published."""


def _fail(message):
    raise BackupArchiveError(message)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _connect(path, *, immutable=False):
    suffix = "?mode=ro" + ("&immutable=1" if immutable else "")
    connection = sqlite3.connect(Path(path).resolve().as_uri() + suffix, uri=True)
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    return connection


def _redact_snapshot(path):
    """Remove legacy task credentials without ever touching the source database."""
    secret_keys = {"cookie", "cookies", "password", "passwd", "pwd", "token",
                   "accesstoken", "refreshtoken", "apikey", "authorization",
                   "secret", "clientsecret", "sessionid", "setcookie"}

    def redact(value):
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()
                    if re.sub(r"[^a-z0-9]", "", key.lower()) not in secret_keys}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA secure_delete=ON")
        # Device grants and pending codes must never revive after a restore.
        for table in ('devices', 'device_pairings'):
            if db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone() == ('table',):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchone():
                    _fail('Cannot safely redact device table with triggers')
                db.execute('DELETE FROM ' + table)
        db.commit()
        if db.execute("SELECT type FROM sqlite_master WHERE name='import_tasks'").fetchone() != ("table",):
            db.execute('VACUUM')
            return
        columns = {row[1] for row in db.execute("PRAGMA table_info(import_tasks)")}
        if not {"id", "config"} <= columns:
            return
        # Updating untrusted snapshots must not run app-defined triggers.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='import_tasks'").fetchone():
            _fail("Cannot safely redact task table with triggers")
        rows = db.execute("SELECT id, config FROM import_tasks").fetchall()
        for task_id, raw in rows:
            if raw is None:
                continue
            config = _json(raw)
            sanitized = redact(config)
            if sanitized != config:
                db.execute("UPDATE import_tasks SET config=? WHERE id=?",
                           (json.dumps(sanitized, ensure_ascii=True), task_id))
        db.commit()
        # Rebuild even without matching keys: deleted historical task records can
        # contain credentials in freelist pages of an otherwise valid snapshot.
        db.execute("VACUUM")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _db_metadata(path):
    with closing(_connect(path, immutable=True)) as db:
        # Reject executable view definitions masquerading as the notes table.
        if db.execute("SELECT type FROM sqlite_master WHERE name='notes'").fetchone() != ("table",):
            _fail("Missing notes table")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            _fail("SQLite integrity_check failed")
        count = db.execute("SELECT count(*) FROM notes").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in db.execute("PRAGMA table_info(notes)")}
        references = set()
        if "images" in columns:
            for (raw,) in db.execute("SELECT images FROM notes"):
                images = [] if raw is None else _json(raw)
                if images is None:  # SQLAlchemy legacy JSON null
                    images = []
                if not isinstance(images, list):
                    _fail("notes.images must be a JSON array")
                for image in images:
                    if not isinstance(image, dict):
                        _fail("Invalid image record")
                    # Older metadata may have only a remote URL, not a local file.
                    if "path" not in image:
                        continue
                    name = image["path"]
                    if not isinstance(name, str) or not _IMAGE.fullmatch(name):
                        _fail("Unsafe attachment reference")
                    references.add("attachments/" + name)
        return count, version, references


def _safe_name(name):
    return name in {"pkm.db", "manifest.json"} or (
        name.startswith("attachments/") and bool(_IMAGE.fullmatch(name[12:])))


def _file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as source:
        while chunk := source.read(_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


def _copy_to_zip(archive, path, name):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        _fail("Backup source must be a regular non-symlink file")
    if before.st_size > MAX_FILE_SIZE:
        _fail("File size limit exceeded")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            _fail("Backup source changed while opening")
        digest = hashlib.sha256()
        size = 0
        with archive.open(name, "w", force_zip64=True) as target:
            while chunk := source.read(_CHUNK):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    _fail("File size limit exceeded")
                digest.update(chunk)
                target.write(chunk)
        if size != opened.st_size:
            _fail("Backup source changed while reading")
    value = digest.hexdigest()
    if name.startswith("attachments/") and value != path.name.split(".")[0]:
        _fail("Attachment content hash mismatch")
    return {"sha256": value, "size": size}


def _publish(temporary, final):
    # Windows rename is atomic and fails if the destination exists. POSIX rename
    # overwrites, so use atomic no-clobber hard-link publication on that platform.
    # Both paths are on the output filesystem; no check-then-overwrite race.
    if os.name == "nt":
        os.rename(temporary, final)
    else:
        os.link(temporary, final)
        os.unlink(temporary)


def create_archive(db_path, attachments_dir, output_dir):
    """Create a verified archive, returning metadata including its absolute path.

    Errors raise BackupArchiveError; no partially written public ZIP is left.
    No migrations, live DB writes, recursive scans or secret configuration reads.
    """
    try:
        db_path = Path(db_path)
        root = Path(attachments_dir)
        output = Path(output_dir).resolve()
        if db_path.is_symlink() or not db_path.is_file():
            _fail("Database source must be a regular file")
        if root.is_symlink():
            _fail("Attachment directory must not be a symlink")
        output.mkdir(parents=True, exist_ok=True)
        created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        filename = "pkm-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex + ".zip"
        final = output / filename
        with tempfile.TemporaryDirectory(prefix=".pkm-backup-", dir=output) as scratch:
            scratch = Path(scratch)
            snapshot = scratch / "pkm.db"
            with closing(_connect(db_path)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            _redact_snapshot(snapshot)
            count, version, references = _db_metadata(snapshot)
            if len(references) + 2 > MAX_ENTRIES:
                _fail("Archive entry limit exceeded")
            temporary = scratch / "archive.tmp"
            manifest = {"format_version": 1, "created": created, "notes_count": count,
                        "schema_version": version, "files": {}}
            total = 0
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for name in ["pkm.db", *sorted(references)]:
                    source_path = snapshot if name == "pkm.db" else root / name[12:]
                    info = _copy_to_zip(archive, source_path, name)
                    total += info["size"]
                    if total > MAX_TOTAL_SIZE:
                        _fail("Archive total size limit exceeded")
                    manifest["files"][name] = info
                raw = json.dumps(manifest, ensure_ascii=True, sort_keys=True).encode("utf-8")
                if len(raw) > MAX_MANIFEST_SIZE or total + len(raw) > MAX_TOTAL_SIZE:
                    _fail("Manifest/total size limit exceeded")
                archive.writestr("manifest.json", raw)
            with open(temporary, "r+b") as handle:
                os.fsync(handle.fileno())
            metadata = verify_archive(temporary)
            _publish(temporary, final)
            metadata.update(path=str(final), filename=filename)
            return metadata
    except BackupArchiveError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BackupArchiveError("Could not create complete backup: " + str(exc)) from exc


def verify_archive(path):
    """Validate only in TemporaryDirectory; never write to an application DB.

    Enforces entry/expanded-byte limits, exact inventory, hashes, content-addressed
    attachment names, notes references/count, schema version and integrity_check.
    Returns manifest plus path, filename, ZIP byte size and ZIP sha256.
    """
    try:
        path = Path(path).resolve()
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                _fail("Archive entry limit exceeded")
            names = set()
            total = 0
            for item in entries:
                name = item.filename
                mode = item.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (item.orig_filename != name or not _safe_name(name) or name in names
                        or item.is_dir() or kind not in (0, stat.S_IFREG)
                        or item.flag_bits & 1):
                    _fail("Unsafe, duplicate, symlink or encrypted archive entry")
                if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    _fail("Unsupported ZIP compression")
                if item.file_size > MAX_FILE_SIZE or item.file_size < 0:
                    _fail("File size limit exceeded")
                total += item.file_size
                if total > MAX_TOTAL_SIZE:
                    _fail("Archive total size limit exceeded")
                names.add(name)
            if not {"pkm.db", "manifest.json"} <= names:
                _fail("Missing archive structure")
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_SIZE:
                _fail("Manifest size limit exceeded")
            manifest = _json(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or set(manifest) != {
                    "format_version", "created", "notes_count", "schema_version", "files"}:
                _fail("Invalid manifest structure")
            if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
                _fail("Unsupported manifest version")
            for key in ("notes_count", "schema_version"):
                if type(manifest[key]) is not int or manifest[key] < 0:
                    _fail("Invalid manifest count/version")
            created = manifest["created"]
            if not isinstance(created, str) or not created.endswith("Z"):
                _fail("Manifest created must be UTC")
            datetime.fromisoformat(created[:-1] + "+00:00")
            files = manifest["files"]
            if not isinstance(files, dict) or set(files) != names - {"manifest.json"}:
                _fail("Manifest inventory mismatch")
            for name, info in files.items():
                if (not isinstance(info, dict) or set(info) != {"sha256", "size"}
                        or type(info["size"]) is not int or info["size"] < 0
                        or not isinstance(info["sha256"], str) or not _HASH.fullmatch(info["sha256"])
                        or info["size"] != archive.getinfo(name).file_size):
                    _fail("Invalid manifest file metadata")
            with tempfile.TemporaryDirectory(prefix="pkm-verify-") as scratch:
                scratch = Path(scratch)
                streamed = archive.getinfo("manifest.json").file_size
                for name, expected in files.items():
                    # name already passed the exact allowlist; do not use extract().
                    destination = scratch / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(name) as source, open(destination, "xb") as target:
                        while chunk := source.read(_CHUNK):
                            size += len(chunk)
                            streamed += len(chunk)
                            if size > MAX_FILE_SIZE or streamed > MAX_TOTAL_SIZE or size > expected["size"]:
                                _fail("Expanded ZIP size limit exceeded")
                            digest.update(chunk)
                            target.write(chunk)
                    if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
                        _fail("File size/hash mismatch")
                    if name.startswith("attachments/") and digest.hexdigest() != name[12:76]:
                        _fail("Attachment content hash mismatch")
                count, version, references = _db_metadata(scratch / "pkm.db")
                if count != manifest["notes_count"] or version != manifest["schema_version"]:
                    _fail("Database count/schema version mismatch")
                if references != names - {"pkm.db", "manifest.json"}:
                    _fail("Database attachment inventory mismatch")
        return {**manifest, **_file_digest(path), "path": str(path), "filename": path.name,
                "files_count": len(files)}
    except BackupArchiveError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, zipfile.BadZipFile,
            RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise BackupArchiveError("Invalid backup archive: " + str(exc)) from exc
