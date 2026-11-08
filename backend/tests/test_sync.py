"""Offline sync contract tests: isolated temporary SQLite, no importer/network calls."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("DEBUG", "false")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.api import attachments, sync
from app.core.database import Base, get_db
from app.core import sync_snapshot
from app.models.note import DataSource, ImportTask, Note
from app.models_membership import NoteSource


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.network_patch = patch("socket.create_connection", side_effect=AssertionError("Network forbidden"))
        self.network_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine("sqlite:///" + str(self.root / "test.db"),
                                    connect_args={"check_same_thread": False})
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.images = self.root / "attachments"
        self.images.mkdir()
        self.data = b"offline image fixture"
        self.filename = hashlib.sha256(self.data).hexdigest() + ".png"
        (self.images / self.filename).write_bytes(self.data)
        with self.sessions() as db:
            db.add(Note(id=1, title="fixture", content="正文", ai_summary="AI", tags=["a"],
                        images=[{"path": self.filename, "url": "https://example.invalid/a", "alt": "图片"}],
                        image_total=1))
            db.add(Note(id=2, title="second", content="public"))
            db.add(ImportTask(platform="zhihu", config={"cookie": "COOKIE_MUST_NOT_LEAK"},
                              error_message="TASK_SECRET_MUST_NOT_LEAK"))
            db.commit()
        self.app = FastAPI()
        self.app.include_router(sync.router, prefix="/api")
        def dependency():
            with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_db] = dependency
        self.root_patch = patch.object(attachments, "ROOT", self.images)
        self.root_patch.start()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.root_patch.stop()
        self.engine.dispose()
        self.temporary.cleanup()
        self.network_patch.stop()

    def manifest(self):
        response = self.client.get("/api/sync/manifest")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        return response.json()

    def batch(self, snapshot, ids):
        return self.client.post("/api/sync/batch", json={"snapshot": snapshot, "ids": ids})

    def test_exact_manifest_batch_canonical_revision_and_secrets(self):
        with patch.dict(os.environ, {"COOKIE": "ENV_MUST_NOT_LEAK", "API_KEY": "KEY_MUST_NOT_LEAK"}):
            manifest = self.manifest()
            response = self.batch(manifest["snapshot"], [2, 1])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([note["id"] for note in body["notes"]], [2, 1])
        self.assertEqual(set(manifest), {"protocol", "snapshot", "notes", "attachments", "scope"})
        self.assertEqual(manifest["protocol"], 1)
        self.assertEqual(manifest["attachments"], [{"filename": self.filename,
            "sha256": self.filename.split(".")[0], "size": len(self.data), "available": True,
            "url": "/api/attachments/" + self.filename}])
        for note in body["notes"]:
            note_revision = note.pop("revision")
            raw = json.dumps(note, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.assertEqual(note_revision, hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest["scope"], {"mode": "all", "source_ids": []})
        # All mode keeps the pre-scope protocol-1 hash input unchanged.
        digest_input = {key: value for key, value in manifest.items() if key not in {"snapshot", "scope"}}
        self.assertEqual(manifest["snapshot"], sync_snapshot.revision(digest_input))
        wire = json.dumps([manifest, body])
        for secret in ("COOKIE_MUST_NOT_LEAK", "TASK_SECRET_MUST_NOT_LEAK", "ENV_MUST_NOT_LEAK", "KEY_MUST_NOT_LEAK"):
            self.assertNotIn(secret, wire)
        self.assertEqual(self.client.get("/api/sync/attachments/" + self.filename).content, self.data)
        for name in ("secrets.env", "0" * 64 + ".txt", "..%2F.env"):
            self.assertEqual(self.client.get("/api/sync/attachments/" + name).status_code, 404)

    def test_body_ai_images_changes_even_with_identical_timestamp_and_deleted_ids(self):
        for field, value in (("content", "changed"), ("ai_summary", "changed AI"),
                             ("images", [{"path": self.filename, "alt": "changed"}])):
            previous = self.manifest()
            with self.sessions() as db:
                old_timestamp = db.get(Note, 1).updated_at
                db.execute(Note.__table__.update().where(Note.id == 1).values(**{field: value, "updated_at": old_timestamp}))
                db.commit()
            changed = self.manifest()
            self.assertNotEqual(previous["snapshot"], changed["snapshot"])
            self.assertNotEqual(previous["notes"][0]["revision"], changed["notes"][0]["revision"])
            conflict = self.batch(previous["snapshot"], [1])
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["detail"]["code"], "snapshot_changed")
            self.assertTrue(conflict.json()["detail"]["restart"])
        before_delete = self.manifest()
        with self.sessions() as db:
            db.delete(db.get(Note, 2))
            db.commit()
        after_delete = self.manifest()
        self.assertEqual([note["id"] for note in after_delete["notes"]], [1])
        self.assertEqual(self.batch(before_delete["snapshot"], [2]).status_code, 409)
        self.assertEqual(self.batch(after_delete["snapshot"], [2]).status_code, 404)

    def test_batch_constraints_and_index_limit(self):
        snapshot = self.manifest()["snapshot"]
        for ids in ([], list(range(1, 52)), [1, 1], [0], [-1], [True], ["1"], [1.0]):
            self.assertEqual(self.batch(snapshot, ids).status_code, 422, repr(ids))
        self.assertEqual(self.batch("bad", [1]).status_code, 422)
        self.assertEqual(self.client.post("/api/sync/batch", json={
            "snapshot": snapshot, "ids": [1], "cookie": "secret"}).status_code, 422)
        with patch.object(sync_snapshot, "MAX_NOTES", 1):
            self.assertEqual(self.client.get("/api/sync/manifest").status_code, 413)
            self.assertEqual(self.batch(snapshot, [1]).status_code, 413)
        with patch.object(sync_snapshot, "MAX_ATTACHMENTS", 0):
            self.assertEqual(self.client.get("/api/sync/manifest").status_code, 413)

    def test_missing_corrupt_symlink_attachments_have_one_contract(self):
        before = self.manifest()
        target = self.images / self.filename
        target.unlink()
        missing = self.manifest()
        self.assertEqual(missing["attachments"][0]["available"], False)
        self.assertIsNone(missing["attachments"][0]["size"])
        self.assertEqual(missing["attachments"][0]["sha256"], self.filename.split(".")[0])
        self.assertNotEqual(before["snapshot"], missing["snapshot"])
        self.assertEqual(self.batch(before["snapshot"], [1]).status_code, 409)
        self.assertEqual(self.client.get("/api/sync/attachments/" + self.filename).status_code, 404)
        target.write_bytes(b"corrupted")
        self.assertEqual(self.manifest(), missing)
        target.unlink()
        try:
            target.symlink_to(self.root / "test.db")
        except OSError:
            return  # Some Windows installations cannot create symlinks.
        self.assertEqual(self.manifest(), missing)
        self.assertEqual(self.client.get("/api/sync/attachments/" + self.filename).status_code, 404)

    def test_sqlite_reader_is_consistent_during_concurrent_commit_then_409(self):
        initial = self.manifest()
        real_entry = sync_snapshot.attachment_entry
        committed = []
        def change_after_notes_read(root, filename):
            if not committed:
                with self.sessions() as writer:
                    writer.execute(Note.__table__.update().where(Note.id == 1).values(content="concurrent change"))
                    writer.commit()
                committed.append(True)
            return real_entry(root, filename)
        with patch.object(sync_snapshot, "attachment_entry", change_after_notes_read):
            response = self.batch(initial["snapshot"], [1, 2])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["snapshot"], initial["snapshot"])
        self.assertEqual(response.json()["notes"][0]["content"], "正文")
        self.assertEqual(self.batch(initial["snapshot"], [1]).status_code, 409)

    def test_explicit_sqlite_begin_and_no_database_writes_or_autoflush(self):
        statements = []
        def record(connection, cursor, statement, params, context, executemany):
            statements.append(statement.strip().upper())
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            with self.sessions() as db:
                db.add(Note(title="pending must not flush", content="not remote"))
                with sync_snapshot.read_session(db) as reader:
                    self.assertTrue(reader.connection().connection.driver_connection.in_transaction)
                    manifest, notes = sync_snapshot.load_snapshot(reader, self.images)
                self.assertEqual(len(notes), 2)
            self.assertIn("BEGIN", statements)
            self.assertTrue(all(item == "BEGIN" or item.startswith("SELECT") for item in statements), statements)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 2)

    def test_existing_global_auth_protects_sync_and_origin_on_batch(self):
        from app.core.auth import hash_password, install_auth
        origin = "https://pkm.test"
        install_auth(self.app, SimpleNamespace(
            PKM_PASSWORD_HASH=hash_password("offline password"), SECRET_KEY="a" * 48,
            PKM_PUBLIC_ORIGIN=origin, PKM_SESSION_TTL_SECONDS=60,
            PKM_LOGIN_MAX_ATTEMPTS=100, PKM_LOGIN_WINDOW_SECONDS=60,
            PKM_TRUSTED_PROXY_PEERS=""))
        with TestClient(self.app, base_url=origin) as client:
            self.assertEqual(client.get("/api/sync/manifest").status_code, 401)
            self.assertEqual(client.get("/api/sync/attachments/" + self.filename).status_code, 401)
            self.assertEqual(client.post("/api/auth/login", json={"password": "offline password"},
                                         headers={"Origin": origin}).status_code, 200)
            manifest = client.get("/api/sync/manifest").json()
            body = {"snapshot": manifest["snapshot"], "ids": [1]}
            self.assertEqual(client.post("/api/sync/batch", json=body).status_code, 403)
            self.assertEqual(client.post("/api/sync/batch", json=body,
                                         headers={"Origin": origin}).status_code, 200)
            self.assertEqual(client.get("/api/sync/attachments/" + self.filename).status_code, 200)

    def test_empty_complete_manifest_and_fifty_id_boundary(self):
        snapshot = self.manifest()["snapshot"]
        self.assertEqual(self.batch(snapshot, list(range(1, 51))).status_code, 404)
        with self.sessions() as db:
            db.query(Note).delete()
            db.commit()
        manifest = self.manifest()
        self.assertEqual(manifest["notes"], [])
        self.assertEqual(manifest["attachments"], [])
        self.assertEqual(self.batch(snapshot, [1]).status_code, 409)
        self.assertEqual(self.batch(manifest["snapshot"], [1]).status_code, 404)

    def test_reader_retains_database_snapshot_across_a_wal_commit(self):
        with self.sessions() as db:
            with sync_snapshot.read_session(db) as reader:
                first = reader.execute(text("SELECT content FROM notes WHERE id=1")).scalar_one()
                with self.sessions() as writer:
                    writer.execute(text("UPDATE notes SET content='concurrent' WHERE id=1"))
                    writer.commit()
                second = reader.execute(text("SELECT content FROM notes WHERE id=1")).scalar_one()
                self.assertEqual(first, second)
        with self.sessions() as db:
            self.assertEqual(db.get(Note, 1).content, "concurrent")

    def seed_memberships(self):
        with self.sessions() as db:
            db.add_all([DataSource(id=value, name=f"source {value}", platform="zhihu",
                                   collection_id=str(value), collection_url=f"https://www.zhihu.com/collection/{value}")
                        for value in (1, 2, 3)])
            db.add_all([NoteSource(source_id=1, note_id=1), NoteSource(source_id=2, note_id=1)])
            # Similar category/title/platform is deliberately not a membership.
            db.get(Note, 2).title = "source 1"
            db.get(Note, 2).source = "zhihu"
            db.get(Note, 2).category = "zhihu_collection"
            db.commit()

    def test_filtered_scopes_memberships_overlap_empty_and_legacy_notes(self):
        self.seed_memberships()
        all_manifest = self.manifest()
        response = self.client.get("/api/sync/manifest?scope=sources&source_ids=2&source_ids=1")
        self.assertEqual(response.status_code, 200, response.text)
        filtered = response.json()
        self.assertEqual(filtered["scope"], {"mode": "sources", "source_ids": [1, 2]})
        self.assertEqual([note["id"] for note in filtered["notes"]], [1])
        self.assertEqual([note["id"] for note in all_manifest["notes"]], [1, 2])
        self.assertEqual(filtered, self.client.get("/api/sync/manifest?source_ids=1&source_ids=2").json())
        self.assertEqual(filtered["snapshot"], sync_snapshot.revision({
            key: value for key, value in filtered.items() if key != "snapshot"}))
        body = {"snapshot": filtered["snapshot"], "ids": [1], "scope": "sources", "source_ids": [2, 1]}
        self.assertEqual(self.client.post("/api/sync/batch", json=body).status_code, 200)
        self.assertEqual(self.batch(filtered["snapshot"], [1]).status_code, 409)
        for ids in ([1], [2], [3], []):
            changed = dict(body, source_ids=ids)
            self.assertEqual(self.client.post("/api/sync/batch", json=changed).status_code, 409)
        outside = dict(body, ids=[2])
        self.assertEqual(self.client.post("/api/sync/batch", json=outside).status_code, 404)
        empty = self.client.get("/api/sync/manifest?scope=sources").json()
        self.assertEqual(empty["scope"], {"mode": "sources", "source_ids": []})
        self.assertEqual((empty["notes"], empty["attachments"]), ([], []))
        self.assertNotEqual(empty["snapshot"], all_manifest["snapshot"])
        self.assertEqual(self.client.post("/api/sync/batch", json={
            "snapshot": empty["snapshot"], "ids": [1], "source_ids": []}).status_code, 404)
        # Large positive IDs are valid but cannot match SQLite signed integer IDs.
        self.assertEqual(self.client.get("/api/sync/manifest?source_ids=" + "9" * 30).json()["notes"], [])

    def test_membership_changes_invalidate_filtered_snapshot_but_not_all(self):
        self.seed_memberships()
        original_all = self.manifest()
        before = self.client.get("/api/sync/manifest?source_ids=1").json()
        with self.sessions() as db:
            db.add(NoteSource(source_id=1, note_id=2))
            db.commit()
        after = self.client.get("/api/sync/manifest?source_ids=1").json()
        self.assertEqual([note["id"] for note in after["notes"]], [1, 2])
        self.assertNotEqual(after["snapshot"], before["snapshot"])
        self.assertNotEqual(after["snapshot"], original_all["snapshot"])
        self.assertEqual(original_all, self.manifest())
        self.assertEqual(self.client.post("/api/sync/batch", json={
            "snapshot": before["snapshot"], "ids": [1], "source_ids": [1]}).status_code, 409)
        self.assertEqual(self.client.post("/api/sync/batch", json={
            "snapshot": after["snapshot"], "ids": [2, 1], "source_ids": [1]}).status_code, 200)

    def test_deleted_source_cannot_match_stale_memberships(self):
        self.seed_memberships()
        with self.sessions() as db:
            db.delete(db.get(DataSource, 1))
            db.commit()
        filtered = self.client.get("/api/sync/manifest?source_ids=1").json()
        self.assertEqual(filtered["notes"], [])
        self.assertEqual([note["id"] for note in self.manifest()["notes"]], [1, 2])

    def test_source_filter_validation_and_thousand_boundary(self):
        snapshot = self.manifest()["snapshot"]
        invalid_queries = ["source_ids=0", "source_ids=-1", "source_ids=true", "source_ids=1.0",
                           "source_ids=", "source_ids=%201", "source_ids=1&source_ids=01",
                           "scope=all&source_ids=1", "scope=invalid"]
        for query in invalid_queries:
            self.assertEqual(self.client.get("/api/sync/manifest?" + query).status_code, 422, query)
        for ids in ([0], [-1], [True], ["1"], [1.0], [1, 1], None, list(range(1, 1002))):
            self.assertEqual(self.client.post("/api/sync/batch", json={
                "snapshot": snapshot, "ids": [1], "source_ids": ids}).status_code, 422, repr(ids))
        for extra in ({"scope": "all", "source_ids": []}, {"scope": None}, {"scope": "invalid"}):
            self.assertEqual(self.client.post("/api/sync/batch", json={
                "snapshot": snapshot, "ids": [1], **extra}).status_code, 422)
        params = [("source_ids", str(value)) for value in range(1, 1001)]
        boundary = self.client.get("/api/sync/manifest", params=params)
        self.assertEqual(boundary.status_code, 200, boundary.text)
        self.assertEqual(len(boundary.json()["scope"]["source_ids"]), 1000)
        self.assertEqual(self.client.get("/api/sync/manifest", params=params + [("source_ids", "1001")]).status_code, 422)
        self.assertEqual(self.client.post("/api/sync/batch", json={
            "snapshot": boundary.json()["snapshot"], "ids": [1],
            "source_ids": list(range(1, 1001))}).status_code, 404)

    def test_sources_expose_only_safe_labels_and_do_not_write(self):
        self.seed_memberships()
        response = self.client.get("/api/sync/sources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.json(), [{"id": value, "name": f"source {value}", "platform": "zhihu"}
                                           for value in (1, 2, 3)])
        self.assertEqual(self.client.post("/api/sync/sources", json={}).status_code, 405)

    def test_canonical_object_key_order_and_array_order(self):
        first = {"images": [{"path": self.filename, "alt": "a"}], "title": "中文"}
        other = {"title": "中文", "images": [{"alt": "a", "path": self.filename}]}
        self.assertEqual(sync_snapshot.revision(first), sync_snapshot.revision(other))
        self.assertNotEqual(sync_snapshot.revision([1, 2]), sync_snapshot.revision([2, 1]))


if __name__ == "__main__":
    unittest.main()
