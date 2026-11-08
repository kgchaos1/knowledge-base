"""Offline migration/schema tests: python -m unittest discover -s tests -p test_migrations.py -v."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Import settings without loading the project's .env or opening its database.
_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as directory:
    try:
        os.chdir(directory)
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "DEBUG": "false"}):
            from app.core.database import Base
            from app.models.note import DataSource, Note
    finally:
        os.chdir(_original_cwd)

from app.core.migrations import migrate_schema
from app.models.schemas import DataSourceCreate, DataSourceResponse, DataSourceUpdate, NoteResponse


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.temp.name) / "migration.db"))

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def create_legacy(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""CREATE TABLE notes (
                id INTEGER PRIMARY KEY, title VARCHAR(500) NOT NULL,
                content TEXT NOT NULL, source VARCHAR(50), source_id VARCHAR(200),
                source_url VARCHAR(1000), tags JSON, category VARCHAR(100),
                ai_summary TEXT, ai_processed INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME
            )""")
            connection.execute(text("""INSERT INTO notes
                (id, title, content, tags, ai_summary, ai_processed)
                VALUES (42, '旧笔记', :content, '[]', :summary, 1)"""),
                {"content": "旧内容\n![图片](https://example.test/1.png)", "summary": "不可丢失的旧摘要"})
            connection.exec_driver_sql("""CREATE TABLE data_sources (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, platform TEXT,
                collection_id TEXT, collection_url TEXT UNIQUE,
                active_task_id INTEGER, last_task_id INTEGER, last_sync_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME
            )""")
            connection.exec_driver_sql("""INSERT INTO data_sources
                (id, name, platform, collection_id, collection_url)
                VALUES (7, '旧收藏夹', 'zhihu', '123', 'https://www.zhihu.com/collection/123')""")

    def assert_note_defaults(self, note):
        self.assertEqual(note.content_status, "unknown")
        self.assertEqual(note.images, [])
        self.assertEqual(note.image_total, 0)
        self.assertEqual(note.image_failed, 0)
        self.assertIsNone(note.content_warning)
        self.assertEqual(note.ai_stale, 0)

    def test_legacy_preserves_data_and_repeated_migration(self):
        self.create_legacy()
        for _ in range(2):
            migrate_schema(self.engine)
        with Session(self.engine) as session:
            note = session.get(Note, 42)
            self.assertEqual(note.id, 42)
            self.assertEqual(note.content, "旧内容\n![图片](https://example.test/1.png)")
            self.assertEqual(note.ai_summary, "不可丢失的旧摘要")
            self.assertEqual(note.ai_processed, 1)
            self.assert_note_defaults(note)
            self.assertEqual(session.get(DataSource, 7).auto_ai, 0)
            self.assertEqual(NoteResponse.model_validate(note).images, [])
            self.assertFalse(DataSourceResponse.model_validate(session.get(DataSource, 7)).auto_ai)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("""UPDATE notes SET content_status='complete',
                images='[{"url":"local.png"}]', image_total=1, ai_stale=1 WHERE id=42""")
            connection.exec_driver_sql("UPDATE data_sources SET auto_ai=1 WHERE id=7")
        migrate_schema(self.engine)
        with Session(self.engine) as session:
            note = session.get(Note, 42)
            self.assertEqual(note.content_status, "complete")
            self.assertEqual(note.images, [{"url": "local.png"}])
            self.assertEqual(note.ai_stale, 1)
            self.assertEqual(session.get(DataSource, 7).auto_ai, 1)

    def test_new_database_before_and_after_create_all(self):
        migrate_schema(self.engine)
        self.assertEqual(inspect(self.engine).get_table_names(), [])
        Base.metadata.create_all(self.engine)
        migrate_schema(self.engine)
        migrate_schema(self.engine)
        with Session(self.engine) as session:
            first = Note(title="first", content="one")
            second = Note(title="second", content="two")
            source = DataSource(name="source", collection_id="123", collection_url="https://www.zhihu.com/collection/123")
            session.add_all([first, second, source])
            session.flush()
            self.assert_note_defaults(first)
            self.assert_note_defaults(second)
            self.assertIsNot(first.images, second.images)
            self.assertEqual(source.auto_ai, 0)
            session.commit()
        # Server defaults must also support non-ORM inserts.
        with self.engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO notes (id, title, content) VALUES (99, 'raw', 'raw')")
            connection.exec_driver_sql("""INSERT INTO data_sources (id, name, platform, collection_id, collection_url)
                VALUES (99, 'raw', 'zhihu', '99', 'https://www.zhihu.com/collection/99')""")
        with Session(self.engine) as session:
            self.assert_note_defaults(session.get(Note, 99))
            self.assertEqual(session.get(DataSource, 99).auto_ai, 0)

    def test_create_all_before_legacy_migration(self):
        self.create_legacy()
        Base.metadata.create_all(self.engine)
        migrate_schema(self.engine)
        with Session(self.engine) as session:
            self.assert_note_defaults(session.get(Note, 42))

    def test_partial_schema_and_missing_source_table(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE notes (id INTEGER PRIMARY KEY, content_status TEXT)")
            connection.exec_driver_sql("INSERT INTO notes VALUES (3, 'partial')")
        migrate_schema(self.engine)
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM notes")).mappings().one()
            self.assertEqual(row["content_status"], "partial")
            self.assertEqual(row["images"], "[]")
            self.assertEqual(row["ai_stale"], 0)
        self.assertNotIn("data_sources", inspect(self.engine).get_table_names())

    def test_ddl_failure_rolls_back_all_added_columns(self):
        self.create_legacy()
        before = {column["name"] for column in inspect(self.engine).get_columns("notes")}

        def fail_mid_migration(conn, cursor, statement, parameters, context, executemany):
            if 'ADD COLUMN "image_total"' in statement:
                raise RuntimeError("simulated migration failure")

        event.listen(self.engine, "before_cursor_execute", fail_mid_migration)
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                migrate_schema(self.engine)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_mid_migration)
        self.assertEqual(before, {column["name"] for column in inspect(self.engine).get_columns("notes")})
        migrate_schema(self.engine)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Note, 42).ai_summary, "不可丢失的旧摘要")

    def test_engine_with_explicit_begin_hook(self):
        @event.listens_for(self.engine, "begin")
        def explicit_begin(connection):
            connection.exec_driver_sql("BEGIN")

        self.create_legacy()
        migrate_schema(self.engine)
        with Session(self.engine) as session:
            self.assert_note_defaults(session.get(Note, 42))


class SchemaTests(unittest.TestCase):
    def test_source_auto_ai_defaults_and_partial_updates(self):
        self.assertFalse(DataSourceCreate(collection_url="123").auto_ai)
        self.assertTrue(DataSourceCreate(collection_url="123", auto_ai=True).auto_ai)
        self.assertIsNone(DataSourceUpdate().auto_ai)
        self.assertEqual(DataSourceUpdate().model_dump(exclude_unset=True), {})
        self.assertEqual(DataSourceUpdate(auto_ai=False).model_dump(exclude_unset=True), {"auto_ai": False})
        self.assertEqual(DataSourceUpdate(auto_ai=True).model_dump(exclude_unset=True), {"auto_ai": True})

    def test_note_response_fields_and_independent_image_defaults(self):
        payload = {"id": 1, "title": "title", "content": "body", "ai_processed": 0,
                   "created_at": "2026-01-01T00:00:00"}
        first = NoteResponse(**payload)
        second = NoteResponse(**payload)
        first.images.append({"url": "image.png", "status": "ok"})
        self.assertEqual(second.images, [])
        self.assertEqual(second.content_status, "unknown")
        self.assertEqual(second.ai_stale, 0)
        self.assertIsNone(second.content_warning)
        enriched = NoteResponse(**payload, content_status="partial", images=[{"url": "image.png"}],
                                image_total=2, image_failed=1, content_warning="missing image", ai_stale=1)
        self.assertEqual(enriched.model_dump()["image_failed"], 1)
        self.assertEqual(enriched.content_warning, "missing image")


if __name__ == "__main__":
    unittest.main()
