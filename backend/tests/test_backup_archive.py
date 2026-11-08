"""Standard library backup regression/security tests; no app startup or network."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import backup_archive as backup


class BackupArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "live.db"
        self.images = self.root / "images"
        self.images.mkdir()
        self.output = self.root / "backups"
        self.data = b"image fixture bytes"
        self.name = hashlib.sha256(self.data).hexdigest() + ".png"
        (self.images / self.name).write_bytes(self.data)

    def make_db(self, images=True, references=None):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("PRAGMA user_version=7")
            db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, content TEXT" + (", images JSON" if images else "") + ")")
            if images:
                refs = [{"path": self.name, "url": "https://example.test/a.png"}] if references is None else references
                db.execute("INSERT INTO notes VALUES (1, 'hello', ?)", (json.dumps(refs),))
            else:
                db.execute("INSERT INTO notes VALUES (1, 'legacy')")

    def create(self):
        return backup.create_archive(self.db, self.images, self.output)

    def rewrite(self, mutate):
        original = self.create()
        with zipfile.ZipFile(original["path"]) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        mutate(entries)
        destination = self.root / "changed.zip"
        with zipfile.ZipFile(destination, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return destination

    def assert_no_publication(self):
        self.assertEqual(list(self.output.iterdir()), [] if self.output.exists() else [])

    def test_roundtrip_exact_inventory_and_metadata(self):
        self.make_db()
        for name in (".env", "password", "Cookie", "unused.png"):
            (self.images / name).write_text("secret")
        result = self.create()
        verified = backup.verify_archive(result["path"])
        self.assertEqual(result, verified)
        self.assertEqual(result["notes_count"], 1)
        self.assertEqual(result["schema_version"], 7)
        self.assertTrue(result["created"].endswith("Z"))
        self.assertEqual(result["size"], Path(result["path"]).stat().st_size)
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertEqual(set(archive.namelist()), {"pkm.db", "manifest.json", "attachments/" + self.name})
        self.assertEqual(set(result["files"]), {"pkm.db", "attachments/" + self.name})

    def test_legacy_notes_without_images_and_missing_attachment_directory(self):
        self.make_db(images=False)
        result = backup.create_archive(self.db, self.root / "absent", self.output)
        self.assertEqual(set(result["files"]), {"pkm.db"})
        self.assertEqual(backup.verify_archive(result["path"])["notes_count"], 1)

    def test_online_backup_includes_uncheckpointed_wal(self):
        self.make_db()
        db = sqlite3.connect(self.db)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO notes VALUES (2, 'wal', '[]')")
            db.commit()
            self.assertEqual(self.create()["notes_count"], 2)
        finally:
            db.close()

    def test_missing_attachment_fails_closed(self):
        self.make_db()
        (self.images / self.name).unlink()
        with self.assertRaises(backup.BackupArchiveError):
            self.create()
        self.assert_no_publication()

    def test_attachment_hash_mismatch_fails_closed(self):
        self.make_db()
        (self.images / self.name).write_bytes(b"corrupt")
        with self.assertRaises(backup.BackupArchiveError):
            self.create()
        self.assert_no_publication()

    def test_polluted_attachment_paths_fail_closed(self):
        self.make_db()
        bad = ["../.env", "../" + self.name, "attachments/" + self.name,
               "C:\\secret.png", "/" + self.name, self.name + ":ads", self.name.upper(), "a" * 64 + ".exe", None]
        for value in bad:
            with self.subTest(value=value):
                with closing(sqlite3.connect(self.db)) as db, db:
                    db.execute("UPDATE notes SET images=?", (json.dumps([{"path": value}]),))
                with self.assertRaises(backup.BackupArchiveError):
                    self.create()
                self.assert_no_publication()

    def test_remote_only_and_duplicate_local_references(self):
        self.make_db(references=[{"url": "https://example.test/remote"}, {"path": self.name}, {"path": self.name}])
        self.assertEqual(len(self.create()["files"]), 2)

    def test_bad_zip_and_missing_structure(self):
        path = self.root / "bad.zip"
        path.write_bytes(b"not a ZIP")
        with self.assertRaises(backup.BackupArchiveError):
            backup.verify_archive(path)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", "{}")
        with self.assertRaises(backup.BackupArchiveError):
            backup.verify_archive(path)

    def test_zip_slip_and_unlisted_files_rejected(self):
        self.make_db()
        for name in ("../escaped", "/abs", "C:/escape", "attachments/../pkm.db", "attachments\\" + self.name, ".env"):
            with self.subTest(name=name):
                changed = self.rewrite(lambda entries: entries.update({name: b"secret"}))
                with self.assertRaises(backup.BackupArchiveError):
                    backup.verify_archive(changed)
        self.assertFalse((self.root / "escaped").exists())

    def test_duplicate_and_symlink_entries_rejected(self):
        self.make_db()
        result = self.create()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(result["path"], "a") as archive:
                archive.writestr("pkm.db", b"duplicate")
        with self.assertRaises(backup.BackupArchiveError):
            backup.verify_archive(result["path"])
        result = self.create()
        with zipfile.ZipFile(result["path"], "a") as archive:
            link = zipfile.ZipInfo("attachments/" + "f" * 64 + ".png")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../../outside")
        with self.assertRaises(backup.BackupArchiveError):
            backup.verify_archive(result["path"])

    def test_hash_count_schema_and_inventory_tampering(self):
        self.make_db()
        def change_manifest(entries, key, value):
            manifest = json.loads(entries["manifest.json"])
            manifest[key] = value
            entries["manifest.json"] = json.dumps(manifest).encode()
        for key, value in (("notes_count", 2), ("schema_version", 99), ("files", {}), ("created", "not-dateZ")):
            with self.subTest(key=key):
                changed = self.rewrite(lambda entries: change_manifest(entries, key, value))
                with self.assertRaises(backup.BackupArchiveError):
                    backup.verify_archive(changed)
        changed = self.rewrite(lambda entries: entries.update({"attachments/" + self.name: b"wrong"}))
        with self.assertRaises(backup.BackupArchiveError):
            backup.verify_archive(changed)

    def test_corrupt_sqlite_rejected_even_with_matching_manifest_digest(self):
        self.make_db()
        def mutate(entries):
            entries["pkm.db"] = b"this is not sqlite"
            manifest = json.loads(entries["manifest.json"])
            manifest["files"]["pkm.db"] = {"size": len(entries["pkm.db"]), "sha256": hashlib.sha256(entries["pkm.db"]).hexdigest()}
            entries["manifest.json"] = json.dumps(manifest).encode()
        with self.assertRaises(backup.BackupArchiveError):
            backup.verify_archive(self.rewrite(mutate))

    def test_expansion_limits(self):
        self.make_db()
        result = self.create()
        for limit, value in (("MAX_ENTRIES", 2), ("MAX_FILE_SIZE", 1), ("MAX_TOTAL_SIZE", 10), ("MAX_MANIFEST_SIZE", 1)):
            with self.subTest(limit=limit), patch.object(backup, limit, value):
                with self.assertRaises(backup.BackupArchiveError):
                    backup.verify_archive(result["path"])

    def test_source_disappears_during_read_fails_closed(self):
        self.make_db()
        original = backup._copy_to_zip
        def remove_then_copy(archive, path, name):
            if name.startswith("attachments/"):
                path.unlink()
            return original(archive, path, name)
        with patch.object(backup, "_copy_to_zip", side_effect=remove_then_copy):
            with self.assertRaises(backup.BackupArchiveError):
                self.create()
        self.assert_no_publication()

    def test_publish_does_not_overwrite(self):
        source = self.root / "source.tmp"
        target = self.root / "exists.zip"
        source.write_bytes(b"new")
        target.write_bytes(b"old")
        with self.assertRaises(FileExistsError):
            backup._publish(source, target)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(source.read_bytes(), b"new")

    def test_snapshot_redacts_nested_legacy_credentials_without_changing_live_db(self):
        self.make_db()
        secret = "UNIQUE_PRIVATE_COOKIE_MARKER_83491"
        config = {"collection_id": "123", "Cookie": secret, "nested": [{"api_key": secret, "password": secret, "safe": True}]}
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE import_tasks (id INTEGER PRIMARY KEY, config JSON)")
            db.execute("INSERT INTO import_tasks VALUES (1, ?)", (json.dumps(config),))
        before = self.db.read_bytes()
        result = self.create()
        self.assertEqual(result["files_count"], 2)
        self.assertEqual(self.db.read_bytes(), before)
        with zipfile.ZipFile(result["path"]) as archive:
            data = archive.read("pkm.db")
        self.assertNotIn(secret.encode(), data)
        snapshot = self.root / "check.db"
        snapshot.write_bytes(data)
        with closing(sqlite3.connect(snapshot)) as db:
            saved = json.loads(db.execute("SELECT config FROM import_tasks").fetchone()[0])
        self.assertEqual(saved, {"collection_id": "123", "nested": [{"safe": True}]})

    def test_invalid_images_json_fails_closed(self):
        self.make_db()
        for raw in ("{", "{}", '[{"path":"a","path":"b"}]'):
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("UPDATE notes SET images=?", (raw,))
            with self.assertRaises(backup.BackupArchiveError):
                self.create()
            self.assert_no_publication()

    def test_verification_does_not_modify_live_database(self):
        self.make_db()
        result = self.create()
        before = self.db.read_bytes()
        backup.verify_archive(result["path"])
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
