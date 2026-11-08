"""Offline regression tests. Run from backend: python -m unittest discover -s tests -v.

No application startup, existing database, credentials, or external HTTP calls.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

# Avoid reading the project's .env or opening its configured database.
_ORIGINAL_CWD = os.getcwd()
_TEMP = tempfile.TemporaryDirectory()
os.chdir(_TEMP.name)
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DEBUG"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.api import import_tasks, sources
from app.core.database import Base, get_db
from app.models.note import DataSource, ImportTask, Note
from app.models.schemas import DataSourceCreate
# Settings/engine have now loaded safely; restore cwd before temp cleanup.
os.chdir(_ORIGINAL_CWD)
from app.importers import zhihu


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.temp.name) / "test.db"), connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        app = FastAPI()
        app.include_router(sources.router, prefix="/api")
        app.include_router(import_tasks.router, prefix="/api")

        def db_dependency():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = db_dependency
        self.client = TestClient(app)
        self.worker_patch = patch.object(import_tasks, "SessionLocal", self.sessions)
        self.worker_patch.start()
        # Guard against accidental real network traffic.
        self.network_patch = patch("requests.sessions.Session.request", side_effect=AssertionError("Network is forbidden in tests"))
        self.network_patch.start()
        self.async_network_patch = patch("httpx.AsyncClient.request", side_effect=AssertionError("Network is forbidden in tests"))
        self.async_network_patch.start()

    def tearDown(self):
        self.async_network_patch.stop()
        self.network_patch.stop()
        self.worker_patch.stop()
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def create(self, collection="123", **extra):
        response = self.client.post("/api/sources/", json={"collection_url": collection, **extra})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_strict_url_validation_and_secrets(self):
        for value in ["http://www.zhihu.com/collection/1", "https://zhihu.com/collection/1", "https://evil.test/collection/1", "https://www.zhihu.com.evil.test/collection/1", "https://www.zhihu.com@evil.test/collection/1", "https://www.zhihu.com/collection/1//", "https://www.zhihu.com:443/collection/1", "https://www.zhihu.com/collection/%31", "123?utm=x", "https://www.zhihu.com/collection/1?x=bad\nvalue", " 1", "1\n", "１２３", "", 123]:
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/sources/", json={"collection_url": value}).status_code, 422)
        for field in ["cookie", "config", "user_id", "token"]:
            self.assertEqual(self.client.post("/api/sources/", json={"collection_url": "123", field: "secret"}).status_code, 422)
        self.assertEqual(self.client.post("/api/sources/", json={"platform": "douyin", "collection_url": "123"}).status_code, 422)
        saved = self.create("00123")
        self.assertEqual(saved["collection_url"], "https://www.zhihu.com/collection/123")
        self.assertNotIn("cookie", saved)
        self.assertEqual(self.client.post("/api/sources/", json={"collection_url": saved["collection_url"]}).status_code, 409)

    def test_copied_url_suffixes_are_normalized_without_persisting_metadata(self):
        variants = ["https://www.zhihu.com/collection/00123/",
                    "https://www.zhihu.com/collection/123?utm_source=share",
                    "https://www.zhihu.com/collection/123#section",
                    "https://www.zhihu.com/collection/123/?cookie=do-not-store#token=secret"]
        source = self.create(variants[0])
        canonical = "https://www.zhihu.com/collection/123"
        self.assertEqual(source["collection_url"], canonical)
        for value in variants[1:]:
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/sources/", json={"collection_url": value}).status_code, 409)
                patched = self.client.patch(f"/api/sources/{source['id']}", json={"collection_url": value})
                self.assertEqual(patched.status_code, 200)
                self.assertEqual(patched.json()["collection_url"], canonical)
        with patch.object(import_tasks, "run_import_task"):
            response = self.client.post("/api/import/", json={"platform": "zhihu", "config": {"collection_url": variants[-1]}})
        self.assertEqual(response.status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.query(DataSource).one().collection_url, canonical)
            self.assertEqual(db.query(ImportTask).one().config, {"collection_url": canonical})

    def test_crud_preserves_notes_and_history(self):
        source = self.create()
        with self.sessions() as db:
            db.add(Note(title="existing", content="keep me", source="zhihu"))
            db.add(ImportTask(platform="zhihu", config={}, status="completed"))
            db.commit()
        self.assertEqual(len(self.client.get("/api/sources/").json()), 1)
        uri = f"/api/sources/{source['id']}"
        changed = self.client.patch(uri, json={"name": "阅读", "collection_url": "456"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["collection_id"], "456")
        for data in [{}, {"name": None}, {"collection_url": None}, {"cookie": "secret"}]:
            self.assertEqual(self.client.patch(uri, json=data).status_code, 422)
        self.assertEqual(self.client.delete(uri).status_code, 200)
        self.assertEqual(self.client.get(uri).status_code, 404)
        self.assertEqual(self.client.post(uri + "/sync").status_code, 404)
        with self.sessions() as db:
            self.assertEqual(db.query(Note).one().content, "keep me")
            self.assertEqual(db.query(ImportTask).count(), 1)

    def test_sync_independent_session_progress_and_repeat(self):
        source = self.create()
        seen = []

        async def importer(config, db):
            seen.append(db)
            self.assertEqual(config, {"collection_url": source["collection_url"], "_enrich": True, "_source_id": source['id']})
            db.add(Note(title="new", content="imported", source="zhihu"))
            db.commit()
            return {"total": 1, "success": 1, "failed": 0}

        with patch.object(import_tasks.settings, "ZHIHU_COOKIE", "configured-only"), patch.object(zhihu, "import_from_zhihu", importer):
            with self.sessions() as request_db:
                background = BackgroundTasks()
                task = asyncio.run(sources.sync_source(source["id"], background, request_db))
                task_id = task.id
                self.assertEqual(background.tasks[0].args, (task_id,))
                self.assertFalse(background.tasks[0].kwargs)
            # Worker starts only AFTER the request Session has been closed.
            asyncio.run(background())
            import_tasks.run_import_task(task_id)  # duplicate dispatch must be a no-op
        self.assertEqual(len(seen), 1)
        self.assertIsNot(seen[0], request_db)
        progress = self.client.get(f"/api/import/{task_id}").json()
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(progress["success_count"], 1)
        saved = self.client.get(f"/api/sources/{source['id']}").json()
        self.assertIsNone(saved["active_task_id"])
        self.assertEqual(saved["last_task_id"], task_id)
        self.assertIsNotNone(saved["last_sync_at"])

    def test_pending_conflicts_and_failure_releases_claim(self):
        source = self.create()
        uri = f"/api/sources/{source['id']}"
        with patch.object(sources, "run_import_task"):
            queued = self.client.post(uri + "/sync")
        self.assertEqual(queued.status_code, 202)
        task_id = queued.json()["id"]
        self.assertEqual(self.client.post(uri + "/sync").status_code, 409)
        self.assertEqual(self.client.patch(uri, json={"name": "x"}).status_code, 409)
        self.assertEqual(self.client.delete(uri).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/import/{task_id}").status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.query(ImportTask).count(), 1)
        with patch.object(import_tasks.settings, "ZHIHU_COOKIE", ""):
            import_tasks.run_import_task(task_id)
        self.assertEqual(self.client.get(f"/api/import/{task_id}").json()["status"], "failed")
        self.assertIsNone(self.client.get(uri).json()["active_task_id"])
        with patch.object(sources, "run_import_task"):
            self.assertEqual(self.client.post(uri + "/sync").status_code, 202)

    def test_concurrent_sync_has_single_winner(self):
        source = self.create()
        barrier = threading.Barrier(2)

        def trigger():
            with self.sessions() as db:
                barrier.wait()
                try:
                    task = asyncio.run(sources.sync_source(source["id"], BackgroundTasks(), db))
                    return (202, task.id)
                except HTTPException as exc:
                    return (exc.status_code, None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: trigger(), range(2)))
        self.assertEqual(sorted(status for status, _ in results), [202, 409])
        with self.sessions() as db:
            self.assertEqual(db.query(ImportTask).count(), 1)
            self.assertEqual(db.query(DataSource).one().active_task_id, db.query(ImportTask).one().id)

    def test_generic_import_rejects_secrets_and_unimplemented_platforms(self):
        for config in [{"collection_url": "123", "cookie": "secret"}, {"collection_url": "123", "Authorization": "secret"}, {"collection_url": "123", "nested": {"token": "secret"}}, {"collection_url": {"cookie": "secret"}}]:
            self.assertEqual(self.client.post("/api/import/", json={"platform": "zhihu", "config": config}).status_code, 422)
        for platform in ["manual", "not-real", "douyin"]:
            self.assertEqual(self.client.post("/api/import/", json={"platform": platform, "config": {}}).status_code, 400)
        with self.sessions() as db:
            self.assertEqual(db.query(ImportTask).count(), 0)
        with patch.object(import_tasks, "run_import_task"):
            response = self.client.post("/api/import/", json={"platform": "zhihu", "config": {"collection_url": "123"}})
        self.assertEqual(response.status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.query(ImportTask).one().config, {"collection_url": "https://www.zhihu.com/collection/123"})

    def test_worker_rolls_back_and_does_not_persist_exception_secrets(self):
        source = self.create()
        async def importer(config, db):
            db.add(Note(title="uncommitted", content="rollback"))
            raise RuntimeError("Cookie: credential-secret")
        with patch.object(import_tasks.settings, "ZHIHU_COOKIE", "credential-secret"), patch.object(zhihu, "import_from_zhihu", importer):
            response = self.client.post(f"/api/sources/{source['id']}/sync")
        with self.sessions() as db:
            task = db.get(ImportTask, response.json()["id"])
            self.assertEqual(task.status, "failed")
            self.assertNotIn("credential-secret", task.error_message)
            self.assertEqual(db.query(Note).count(), 0)
            self.assertIsNone(db.get(DataSource, source["id"]).active_task_id)

    def test_partial_import_is_explicit(self):
        source = self.create()
        async def importer(config, db):
            return {"total": 100, "success": 80, "failed": 1, "skipped": 9,
                    "duplicates": 10, "truncated": True, "status": "partial",
                    "message": "must not persist arbitrary credential text"}
        with patch.object(import_tasks.settings, "ZHIHU_COOKIE", "configured"), patch.object(zhihu, "import_from_zhihu", importer):
            response = self.client.post(f"/api/sources/{source['id']}/sync")
        progress = self.client.get(f"/api/import/{response.json()['id']}").json()
        self.assertEqual(progress["status"], "partial")
        self.assertEqual(progress["total_count"], 100)
        self.assertIn("truncated=True", progress["error_message"])
        self.assertNotIn("credential", progress["error_message"])
        saved = self.client.get(f"/api/sources/{source['id']}").json()
        self.assertIsNone(saved["active_task_id"])
        self.assertIsNotNone(saved["last_sync_at"])

    def test_auto_ai_can_be_enabled_and_disabled(self):
        from app.ai.service import ai_service
        source = self.create()
        async def importer(config, db):
            note = Note(title='test', content='fixed example', source='zhihu')
            db.add(note)
            db.commit()
            return {'total':1, 'success':1, 'failed':0, 'changed_note_ids':[note.id]}
        from unittest.mock import AsyncMock
        processor = AsyncMock(return_value={'ai_summary':'structured summary','ai_processed':1})
        with patch.object(import_tasks.settings, 'ZHIHU_COOKIE', 'offline'), patch.object(zhihu, 'import_from_zhihu', importer), patch.object(ai_service, 'process_note', processor):
            for enabled in (True, False):
                response = self.client.patch(f"/api/sources/{source['id']}", json={'auto_ai':enabled})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['auto_ai'], enabled)
                self.client.post(f"/api/sources/{source['id']}/sync")
            self.assertEqual(processor.await_count,1)
        with self.sessions() as db:
            notes = db.query(Note).order_by(Note.id).all()
            self.assertEqual(notes[0].ai_processed,1)
            self.assertEqual(notes[1].ai_processed,0)

    def test_sqlite_upgrade_is_additive(self):
        legacy = create_engine("sqlite://")
        try:
            Note.__table__.create(legacy)
            ImportTask.__table__.create(legacy)
            with legacy.begin() as conn:
                conn.execute(text("INSERT INTO notes (title, content) VALUES ('legacy', 'untouched')"))
                conn.execute(text("INSERT INTO import_tasks (platform, status) VALUES ('manual', 'failed')"))
            before = {name: [c["name"] for c in inspect(legacy).get_columns(name)] for name in ["notes", "import_tasks"]}
            Base.metadata.create_all(legacy)
            for name, columns in before.items():
                self.assertEqual([c["name"] for c in inspect(legacy).get_columns(name)], columns)
            with legacy.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT content FROM notes")).scalar_one(), "untouched")
                self.assertEqual(conn.execute(text("SELECT platform FROM import_tasks")).scalar_one(), "manual")
            self.assertIn("data_sources", inspect(legacy).get_table_names())
        finally:
            legacy.dispose()


if __name__ == "__main__":
    unittest.main()
