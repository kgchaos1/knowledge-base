"""Offline file-SQLite scheduler regression tests, no application lifespan."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

# This fixture module safely loads settings without touching the project DB.
import test_sources as fixtures
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.api import sources, import_tasks
from app.core.database import Base
from app.core.migrations import migrate_schema
from app.core.source_scheduler import SourceScheduler, recover_interrupted_tasks
from app.models.note import DataSource, ImportTask
from app.models.schemas import DataSourceCreate, DataSourceUpdate, DataSourceResponse

UTC = timezone.utc


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + str(Path(self.temp.name) / 'scheduler.db'), connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.now = datetime.now(UTC)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def source(self, *, enabled=True, due=True, collection='123'):
        with self.sessions() as db:
            source = DataSource(name='offline', platform='zhihu', collection_id=collection,
                collection_url='https://www.zhihu.com/collection/' + collection,
                sync_enabled=enabled, sync_interval_minutes=60,
                next_sync_at=self.now - timedelta(minutes=1) if due else self.now + timedelta(minutes=60))
            db.add(source)
            db.commit()
            return source.id

    def complete(self, task_id):
        with self.sessions() as db:
            task = db.get(ImportTask, task_id)
            task.status = 'completed'
            db.commit()

    def test_due_disabled_future_and_no_minutely_retry(self):
        due = self.source()
        self.source(enabled=False, collection='456')
        self.source(due=False, collection='789')
        scheduler = SourceScheduler(self.sessions, runner=self.complete)
        ids = asyncio.run(scheduler.run_once(self.now))
        self.assertEqual(len(ids), 1)
        with self.sessions() as db:
            source = db.get(DataSource, due)
            self.assertEqual(source.last_task_id, ids[0])
            self.assertIsNone(source.active_task_id)
            self.assertEqual(source.next_sync_at, (self.now + timedelta(minutes=60)).replace(tzinfo=None))
        self.assertEqual(asyncio.run(scheduler.run_once(self.now + timedelta(minutes=1))), [])

    def test_failure_has_visible_state_and_persisted_next_due(self):
        source_id = self.source()
        def failure(task_id):
            raise RuntimeError('secret-token-not-to-persist')
        scheduler = SourceScheduler(self.sessions, runner=failure)
        task_id = asyncio.run(scheduler.run_once(self.now))[0]
        with self.sessions() as db:
            task = db.get(ImportTask, task_id)
            self.assertEqual(task.status, 'failed')
            self.assertNotIn('secret-token', task.error_message)
            source = db.get(DataSource, source_id)
            self.assertIsNone(source.active_task_id)
            self.assertGreater(source.next_sync_at, self.now.replace(tzinfo=None))
        self.assertEqual(asyncio.run(scheduler.run_once(self.now + timedelta(minutes=1))), [])

    def test_real_worker_mock_importer_uses_independent_session(self):
        source_id = self.source()
        seen = []
        async def importer(config, db):
            seen.append((db, threading.get_ident()))
            return {'total': 2, 'success': 2, 'failed': 0}
        from app.importers import zhihu
        with patch.object(import_tasks, 'SessionLocal', self.sessions), patch.object(import_tasks.settings, 'ZHIHU_COOKIE', 'offline'), patch.object(zhihu, 'import_from_zhihu', importer), patch('requests.sessions.Session.request', side_effect=AssertionError('No network')), patch('httpx.AsyncClient.request', side_effect=AssertionError('No network')):
            ids = asyncio.run(SourceScheduler(self.sessions).run_once(self.now))
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0][1], threading.get_ident())
        with self.sessions() as db:
            self.assertIsNot(seen[0][0], db)
            self.assertEqual(db.get(ImportTask, ids[0]).status, 'completed')
            self.assertIsNone(db.get(DataSource, source_id).active_task_id)

    def test_restart_fails_interrupted_tasks_preserves_history_and_due(self):
        source_id = self.source()
        with self.sessions() as db:
            for status in ['pending', 'running', 'completed']:
                db.add(ImportTask(platform='zhihu', status=status, config={}))
            db.flush()
            source = db.get(DataSource, source_id)
            source.active_task_id = 2
            source.last_task_id = 2
            db.commit()
        self.assertEqual(recover_interrupted_tasks(self.sessions), 2)
        self.assertEqual(recover_interrupted_tasks(self.sessions), 0)
        with self.sessions() as db:
            self.assertEqual([t.status for t in db.query(ImportTask).order_by(ImportTask.id)], ['failed', 'failed', 'completed'])
            source = db.get(DataSource, source_id)
            self.assertIsNone(source.active_task_id)
            self.assertEqual(source.last_task_id, 2)
            self.assertIsNotNone(source.next_sync_at)

    def test_concurrent_manual_and_scheduled_have_one_winner(self):
        source_id = self.source()
        barrier = threading.Barrier(2)
        def claim(scheduled):
            with self.sessions() as db:
                barrier.wait()
                task = sources.claim_source_sync(db, source_id, scheduled_at=self.now if scheduled else None)
                return task.id if task else None
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(claim, [True, False]))
        self.assertEqual(sum(task_id is not None for task_id in ids), 1)
        with self.sessions() as db:
            self.assertEqual(db.query(ImportTask).count(), 1)

    def test_interval_bounds_defaults_and_utc_response(self):
        from pydantic import ValidationError
        for value in [59, 10081, 60.5, True, '60', None]:
            for schema in [DataSourceCreate, DataSourceUpdate]:
                with self.subTest(value=value, schema=schema.__name__), self.assertRaises(ValidationError):
                    schema(**({'collection_url': '123'} if schema is DataSourceCreate else {}), sync_interval_minutes=value)
        for value in [60, 10080]:
            self.assertEqual(DataSourceCreate(collection_url='123', sync_interval_minutes=value).sync_interval_minutes, value)
        default = DataSourceCreate(collection_url='123')
        self.assertFalse(default.auto_ai)
        self.assertFalse(default.sync_enabled)
        self.assertEqual(default.sync_interval_minutes, 1440)
        with self.sessions() as db:
            created = asyncio.run(sources.create_source(default, db))
            self.assertIsNone(created.next_sync_at)
            changed = asyncio.run(sources.update_source(created.id, DataSourceUpdate(sync_enabled=True), db))
            response = DataSourceResponse.model_validate(changed)
            self.assertEqual(response.next_sync_at.utcoffset(), timedelta(0))
            disabled = asyncio.run(sources.update_source(created.id, DataSourceUpdate(sync_enabled=False), db))
            self.assertIsNone(disabled.next_sync_at)

    def test_disable_preserves_history_but_active_updates_stay_forbidden(self):
        source_id = self.source()
        with self.sessions() as db:
            task = sources.claim_source_sync(db, source_id)
            task_id = task.id
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(sources.update_source(source_id, DataSourceUpdate(sync_enabled=False), db))
            self.assertEqual(caught.exception.status_code, 409)
        self.complete(task_id)
        with self.sessions() as db:
            db.get(DataSource, source_id).active_task_id = None
            db.commit()
            asyncio.run(sources.update_source(source_id, DataSourceUpdate(sync_enabled=False), db))
            self.assertEqual(db.get(ImportTask, task_id).status, 'completed')
            self.assertEqual(db.get(DataSource, source_id).last_task_id, task_id)
        self.assertEqual(asyncio.run(SourceScheduler(self.sessions, runner=self.complete).run_once(self.now)), [])

    def test_api_schedule_fields_bounds_and_read_only_due(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.database import get_db
        app = FastAPI()
        app.include_router(sources.router, prefix='/api')
        def dependency():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = dependency
        with TestClient(app) as client:
            for value in [59, 10081, None, 60.5]:
                response = client.post('/api/sources/', json={'collection_url': '123', 'sync_interval_minutes': value})
                self.assertEqual(response.status_code, 422)
            response = client.post('/api/sources/', json={'collection_url': '123', 'sync_enabled': True, 'sync_interval_minutes': 60})
            self.assertEqual(response.status_code, 201)
            saved = response.json()
            self.assertTrue(saved['sync_enabled'])
            self.assertFalse(saved['auto_ai'])
            self.assertEqual(saved['sync_interval_minutes'], 60)
            self.assertTrue(saved['next_sync_at'].endswith('Z'))
            uri = '/api/sources/' + str(saved['id'])
            for payload in [{'sync_interval_minutes': 59}, {'sync_interval_minutes': 10081}, {'sync_enabled': None}, {'next_sync_at': None}]:
                self.assertEqual(client.patch(uri, json=payload).status_code, 422)
            disabled = client.patch(uri, json={'sync_enabled': False, 'sync_interval_minutes': 10080})
            self.assertEqual(disabled.status_code, 200)
            self.assertIsNone(disabled.json()['next_sync_at'])
            self.assertEqual(client.get(uri).json()['sync_interval_minutes'], 10080)

    def test_additive_legacy_migration(self):
        legacy = create_engine('sqlite://')
        try:
            with legacy.begin() as conn:
                conn.execute(text('CREATE TABLE data_sources (id INTEGER PRIMARY KEY, name TEXT, auto_ai INTEGER NOT NULL DEFAULT 0)'))
                conn.execute(text("INSERT INTO data_sources (name, auto_ai) VALUES ('legacy', 1)"))
            migrate_schema(legacy)
            migrate_schema(legacy)
            with legacy.connect() as conn:
                row = conn.execute(text('SELECT * FROM data_sources')).mappings().one()
                self.assertEqual(row['name'], 'legacy')
                self.assertEqual(row['auto_ai'], 1)
                self.assertEqual(row['sync_enabled'], 0)
                self.assertEqual(row['sync_interval_minutes'], 1440)
                self.assertIsNone(row['next_sync_at'])
        finally:
            legacy.dispose()

    def test_slow_import_does_not_block_polling_other_sources(self):
        self.source()
        first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
        def runner(task_id):
            with self.sessions() as db:
                collection = db.get(ImportTask, task_id).config['collection_url']
            if collection.endswith('/123'):
                first_entered.set()
                if not release.wait(5):
                    raise TimeoutError('test importer did not finish')
            else:
                second_entered.set()
            self.complete(task_id)
        async def scenario():
            scheduler = SourceScheduler(self.sessions, runner=runner, poll_seconds=0.02)
            await scheduler.start()
            try:
                self.assertTrue(await asyncio.to_thread(first_entered.wait, 3))
                self.source(collection='456')
                self.assertTrue(await asyncio.to_thread(second_entered.wait, 3))
                with self.sessions() as db:
                    self.assertEqual(db.query(ImportTask).count(), 2)
            finally:
                release.set()
                await scheduler.stop()
        asyncio.run(scenario())

    def test_shutdown_drains_thread_and_retains_active_claim(self):
        self.source()
        entered, release = threading.Event(), threading.Event()
        def blocking_runner(task_id):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test did not release importer')
            self.complete(task_id)
        async def scenario():
            scheduler = SourceScheduler(self.sessions, runner=blocking_runner)
            await scheduler.start()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                stopping = asyncio.create_task(scheduler.stop())
                await asyncio.sleep(0)
                self.assertFalse(stopping.done())
                with self.sessions() as db:
                    self.assertIsNotNone(db.query(DataSource).one().active_task_id)
                # This coroutine can run while the importer blocks its thread.
                release.set()
                await stopping
                with self.sessions() as db:
                    self.assertEqual(db.query(ImportTask).one().status, 'completed')
                    self.assertIsNone(db.query(DataSource).one().active_task_id)
            finally:
                release.set()
                await scheduler.stop()
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
