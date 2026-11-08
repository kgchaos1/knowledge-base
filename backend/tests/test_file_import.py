"""Offline multipart file-import regressions; no production DB/startup or network."""
import asyncio
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_ORIGINAL_CWD = os.getcwd()
_TEMP = tempfile.TemporaryDirectory()
os.chdir(_TEMP.name)
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DEBUG"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api import file_import
from app.core.database import Base, get_db
from app.models.note import DataSource, Note
os.chdir(_ORIGINAL_CWD)


class FileImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.temp.name) / "test.db"), connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        app = FastAPI()
        app.include_router(file_import.router, prefix="/api")

        def db_dependency():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = db_dependency
        self.client = TestClient(app)
        self.network = patch("httpx.AsyncClient.request", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.sync_network = patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        self.sync_network.start()

    def tearDown(self):
        self.sync_network.stop()
        self.network.stop()
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def upload(self, endpoint, content, filename="note.md", **kwargs):
        return self.client.post("/api/file-import/" + endpoint, files={"file": (filename, content, "application/octet-stream")}, **kwargs)

    def test_preview_bom_unicode_heading_basename_and_exact_contract(self):
        content = "intro\r\n# 中文标题 #\r\n正文 ![图](https://images.invalid/private.jpg)\r\n"
        response = self.upload("preview", b"\xef\xbb\xbf" + content.encode(), "../../nested/中文.MARKDOWN")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "filename": "中文.MARKDOWN", "title": "中文标题", "chars": len(content),
            "format": "markdown", "images_remote_warning": True,
            "content_hash": hashlib.sha256(content.replace("\r\n", "\n").encode()).hexdigest(),
        })
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 0)
        fallback = self.upload("preview", b"plain text", "C:\\private\\example.txt").json()
        self.assertEqual(fallback["filename"], "example.txt")
        self.assertEqual(fallback["title"], "example")
        self.assertEqual(fallback["format"], "text")
        self.assertFalse(fallback["images_remote_warning"])

    def test_commit_preserves_inert_content_and_never_calls_ai_or_network(self):
        marker = Path(self.temp.name) / "executed"
        content = ("---\nsecret: local-secret-do-not-execute\n"
                   "run: !!python/object/apply:os.system ['echo pwned']\n---\n"
                   "# 中文标题\r\n<script>fetch('https://evil.invalid')</script>\n"
                   f"{{{{ __import__('pathlib').Path({str(marker)!r}).touch() }}}}\n"
                   '<img src="https://evil.invalid/image.png">\n')
        with patch("app.ai.service.ai_service.process_note", side_effect=AssertionError("AI forbidden")):
            response = self.upload("commit", content.encode())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "imported")
        self.assertEqual(response.json()["title"], "中文标题")
        self.assertEqual(response.json()["characters"], len(content))
        self.assertFalse(marker.exists())
        with self.sessions() as db:
            note = db.query(Note).one()
            self.assertEqual(note.content, content)
            self.assertEqual(note.source, "file")
            self.assertEqual(note.content_status, "full")
            self.assertEqual(note.ai_processed, 0)
            self.assertEqual(note.images, [])
            self.assertIsNone(note.source_url)
            self.assertEqual(db.query(DataSource).count(), 0)

    def test_same_hash_deduplicates_bom_and_newlines_without_overwriting(self):
        original = "# heading\r\n正文\r\n"
        first = self.upload("commit", original.encode(), data={"title": "用户中文标题"}).json()
        second = self.upload("commit", b"\xef\xbb\xbf" + original.replace("\r\n", "\n").encode(), "different.txt", data={"title": "不覆盖"}).json()
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second, {**first, "status": "duplicate"})
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 1)
            self.assertEqual(db.query(Note).one().content, original)

    def test_size_boundary_and_missing_file(self):
        boundary = b"a" * file_import.MAX_FILE_BYTES
        self.assertEqual(self.upload("preview", boundary).status_code, 200)
        for endpoint in ["preview", "commit"]:
            with self.subTest(endpoint=endpoint):
                response = self.upload(endpoint, boundary + b"a")
                self.assertEqual(response.status_code, 413)
                self.assertEqual(self.client.post("/api/file-import/" + endpoint).status_code, 422)
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 0)

    def test_invalid_inputs_do_not_echo_secrets(self):
        cases = [(b"local-secret\xff", "secret.md", 422),
                 (b"local-secret\x00", "secret.txt", 422),
                 (b"local-secret", "secret.html", 415),
                 (b"", "note.md", 422), (b" \r\n", "note.txt", 422)]
        for endpoint in ["preview", "commit"]:
            for content, filename, status in cases:
                with self.subTest(endpoint=endpoint, filename=filename, status=status):
                    response = self.upload(endpoint, content, filename)
                    self.assertEqual(response.status_code, status)
                    self.assertNotIn("local-secret", response.text)
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 0)

    def test_title_validation_and_html_is_literal_text(self):
        for title in ["x" * 501, "secret\x00bad", "secret\nbad"]:
            response = self.upload("commit", b"body", data={"title": title})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("secret", response.text)
        content = b"<h1>not a Markdown heading</h1><script>throw 'no';</script>"
        response = self.upload("commit", content, "html-as-text.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "html-as-text")
        with self.sessions() as db:
            self.assertEqual(db.query(Note).one().content, content.decode())

    def test_stream_reads_stop_at_limit_without_trusting_size(self):
        class Stream:
            filename = "safe.md"
            size = 1
            consumed = 0
            closed = False

            async def read(self, size):
                self.assertion(size)
                self.consumed += size
                return b"a" * size

            def assertion(self, size):
                if size < 1 or size > 65536:
                    raise AssertionError("unbounded read")

            async def close(self):
                self.closed = True

        stream = Stream()
        with self.assertRaises(HTTPException) as error:
            asyncio.run(file_import._read_text_upload(stream))
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(stream.consumed, file_import.MAX_FILE_BYTES + 1)
        self.assertTrue(stream.closed)

    def test_database_error_is_sanitized_and_rolled_back(self):
        from sqlalchemy.exc import SQLAlchemyError
        with patch("sqlalchemy.orm.Session.commit", side_effect=SQLAlchemyError("local-secret-content")), patch("sqlalchemy.orm.Session.rollback", autospec=True) as rollback:
            response = self.upload("commit", b"local-secret-content")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("local-secret", response.text)
        rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
