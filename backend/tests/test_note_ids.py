"""SQLite note identity regressions, including old databases without AUTOINCREMENT.

Run from backend: python -m unittest discover -s tests -p test_note_ids.py -v
All databases are temporary; no application startup or network access is needed.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as directory:
    try:
        os.chdir(directory)
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "DEBUG": "false"}):
            from app.core.database import Base
            from app.models.note import Note, _allocate_sqlite_note_id
    finally:
        os.chdir(_original_cwd)

from app.core.migrations import migrate_schema


class NoteIdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite:///" + str(Path(self.temp.name) / "note-ids.db")
        self.engine = create_engine(self.url, connect_args={"check_same_thread": False, "timeout": 10})

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def legacy_database(self):
        # Reproduce the original table, not a newly AUTOINCREMENT-enabled one.
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
                VALUES (7, '保留笔记', '原始内容', '["标签"]', '原始摘要', 1),
                       (42, '最高笔记', '删除此条', '[]', NULL, 0)"""))
            original_rootpage = connection.exec_driver_sql(
                "SELECT rootpage FROM sqlite_master WHERE name='notes'"
            ).scalar_one()
        migrate_schema(self.engine)
        return original_rootpage

    def insert_note(self, title="new", explicit_id=None):
        with Session(self.engine) as session:
            note = Note(title=title, content="body", id=explicit_id)
            session.add(note)
            session.flush()
            note_id = note.id
            session.commit()
            return note_id

    def delete_note(self, note_id):
        with Session(self.engine) as session:
            session.delete(session.get(Note, note_id))
            session.commit()

    def watermark(self):
        with self.engine.connect() as connection:
            return connection.exec_driver_sql(
                "SELECT value FROM id_allocator WHERE name='note'"
            ).scalar_one()

    def test_legacy_delete_maximum_never_reuses_and_preserves_data(self):
        original_rootpage = self.legacy_database()
        self.delete_note(42)
        first = self.insert_note()
        self.assertEqual(first, 43)
        self.delete_note(first)
        self.assertEqual(self.insert_note(), 44)
        with self.engine.connect() as connection:
            ddl, rootpage = connection.exec_driver_sql(
                "SELECT sql, rootpage FROM sqlite_master WHERE name='notes'"
            ).one()
            self.assertNotIn("AUTOINCREMENT", ddl.upper())
            self.assertEqual(rootpage, original_rootpage)
        with Session(self.engine) as session:
            kept = session.get(Note, 7)
            self.assertEqual((kept.title, kept.content, kept.tags, kept.ai_summary, kept.ai_processed),
                             ("保留笔记", "原始内容", ["标签"], "原始摘要", 1))

    def test_repeat_migration_and_engine_restart_do_not_reset_watermark(self):
        self.legacy_database()
        high = self.insert_note(explicit_id=500)
        self.delete_note(high)
        self.engine.dispose()
        self.engine = create_engine(self.url)
        for _ in range(3):
            migrate_schema(self.engine)
        self.assertEqual(self.watermark(), 500)
        self.assertEqual(self.insert_note(), 501)
        with Session(self.engine) as session:
            for note in session.query(Note).all():
                session.delete(note)
            session.commit()
        migrate_schema(self.engine)
        self.assertEqual(self.insert_note(), 502)

    def test_two_sessions_on_independent_engines_allocate_unique_ids(self):
        self.legacy_database()
        self.delete_note(42)
        other_engine = create_engine(self.url, connect_args={"check_same_thread": False, "timeout": 10})
        barrier = threading.Barrier(2)

        def worker(engine, label):
            with Session(engine) as session:
                note = Note(title=label, content="concurrent")
                session.add(note)
                barrier.wait(timeout=10)
                session.flush()
                result = note.id
                session.commit()
                return result

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                left = executor.submit(worker, self.engine, "worker-one")
                right = executor.submit(worker, other_engine, "worker-two")
                self.assertEqual(sorted([left.result(timeout=15), right.result(timeout=15)]), [43, 44])
        finally:
            other_engine.dispose()
        with Session(self.engine) as session:
            self.assertEqual(session.query(Note).count(), 3)
            self.assertEqual({note.title for note in session.query(Note).filter(Note.id > 7)},
                             {"worker-one", "worker-two"})

    def test_explicit_ids_advance_watermark_and_collision_never_overwrites(self):
        self.legacy_database()
        self.assertEqual(self.insert_note(explicit_id=100), 100)
        self.assertEqual(self.insert_note(explicit_id=3), 3)
        self.assertEqual(self.watermark(), 100)
        with self.assertRaises(IntegrityError):
            self.insert_note(title="must not overwrite", explicit_id=7)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Note, 7).content, "原始内容")
            self.assertEqual(session.get(Note, 7).title, "保留笔记")
        self.delete_note(100)
        self.assertEqual(self.insert_note(), 101)

    def test_fresh_create_all_needs_no_migration_and_has_native_autoincrement(self):
        Base.metadata.create_all(self.engine)
        first = self.insert_note()
        self.delete_note(first)
        self.assertGreater(self.insert_note(), first)
        with self.engine.connect() as connection:
            ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='notes'").scalar_one()
            self.assertIn("AUTOINCREMENT", ddl.upper())

    def test_multiple_pending_notes_get_distinct_ids_without_returning(self):
        self.legacy_database()
        statements = []

        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", record)
        try:
            with Session(self.engine) as session:
                notes = [Note(title=str(index), content="batch") for index in range(3)]
                session.add_all(notes)
                session.commit()
                self.assertEqual([note.id for note in notes], [43, 44, 45])
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        allocator_statements = [sql for sql in statements if "id_allocator" in sql]
        self.assertTrue(allocator_statements)
        self.assertFalse(any("RETURNING" in sql.upper() or "ON CONFLICT" in sql.upper()
                             for sql in allocator_statements))

    def test_rollback_does_not_leave_partial_note_or_corrupt_allocator(self):
        self.legacy_database()
        with Session(self.engine) as session:
            session.add(Note(title="rollback", content="not committed"))
            session.flush()
            session.rollback()
        self.assertEqual(self.watermark(), 42)
        self.assertEqual(self.insert_note(), 43)

    def test_exhausted_range_fails_without_float_or_id_reuse(self):
        self.legacy_database()
        maximum = 9223372036854775807
        self.insert_note(explicit_id=maximum)
        self.delete_note(maximum)
        with self.assertRaisesRegex(OverflowError, "exhausted"):
            self.insert_note()
        self.assertEqual(self.watermark(), maximum)
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql(
                "SELECT typeof(value) FROM id_allocator WHERE name='note'"
            ).scalar_one(), "integer")

    def test_other_dialects_leave_id_generation_unchanged(self):
        from types import SimpleNamespace
        note = Note(title="postgres", content="unchanged")
        connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        _allocate_sqlite_note_id(None, connection, note)
        self.assertIsNone(note.id)


if __name__ == "__main__":
    unittest.main()
