"""Offline bookmark import contract/security regressions on real temporary SQLite."""
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
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("DEBUG", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from app.api import bookmark_import, sync
from app.core.database import Base, get_db
from app.models.note import DataSource, Note
os.chdir(_ORIGINAL_CWD)


def html(*urls):
    return '<!DOCTYPE NETSCAPE-Bookmark-file-1><DL>' + ''.join(
        f'<DT><A HREF="{url}">Title {index}</A>' for index, url in enumerate(urls)) + '</DL>'


class BookmarkImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.temp.name) / "test.db"),
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        app = FastAPI()
        app.include_router(bookmark_import.router, prefix="/api")
        app.include_router(sync.router, prefix="/api")
        def dependency():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = dependency
        self.client = TestClient(app)
        self.network = patch("socket.create_connection", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.dns = patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden"))
        self.dns.start()

    def tearDown(self):
        self.dns.stop()
        self.network.stop()
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def upload(self, endpoint, content, filename="bookmarks.html", **kwargs):
        if isinstance(content, str):
            content = content.encode("utf-8")
        return self.client.post("/api/bookmark-import/" + endpoint,
                                files={"file": (filename, content, "application/octet-stream")}, **kwargs)

    def test_preview_bom_contract_counts_and_twenty_items_no_db_writes(self):
        content = html(*[f"https://Example.COM:443/path/{i}#fragment" for i in range(23)],
                       "https://example.com/path/0#different", "javascript:alert(1)")
        response = self.upload("preview", b"\xef\xbb\xbf" + content.encode(), "../../nested/收藏.HTM")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"filename", "total", "valid", "skipped", "duplicates_in_file", "items", "content_hash"})
        self.assertEqual((body["filename"], body["total"], body["valid"], body["skipped"], body["duplicates_in_file"]),
                         ("收藏.HTM", 25, 23, 1, 1))
        self.assertEqual(len(body["items"]), 20)
        self.assertEqual(body["items"][0], {"title": "Title 0", "url": "https://example.com/path/0", "folder": ""})
        self.assertEqual(body["content_hash"], hashlib.sha256(b"\xef\xbb\xbf" + content.encode()).hexdigest())
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 0)

    def test_strict_url_rejections_and_normalization_without_dns(self):
        rejected = ["javascript:alert(1)", "data:text/html,evil", "file:///secret", "ftp://example.com/a",
                    "//example.com/a", "/relative", "https:///missing", "https://a:b@example.com/a",
                    "https://localhost/", "http://sub.LOCALHOST./", "http://127.0.0.1/",
                    "http://10.0.0.2/", "http://192.168.0.1/", "http://169.254.169.254/",
                    "http://0.0.0.0/", "http://[::1]/", "http://[fc00::1]/",
                    "http://[::ffff:127.0.0.1]/", "http://[2001:db8::1]/", "http://224.0.0.1/",
                    "http://[2606:4700:4700::1111]suffix/", "http://[::ffff:224.0.0.1]/",
                    "http://2130706433/", "http://127.1/", "http://0177.0.0.1/", "http://0x7f000001/",
                    "http://bad host/", "http://example.com/\nsecret", "http://example.com/%0dsecret",
                    "http://example.com:\\secret", "http://example.com:99999/", "http://example.com:/",
                    "http://-bad.example/", "http://example.com/" + "a" * 2000]
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(bookmark_import.canonicalize_url(url))
        accepted = {"HTTPS://EXAMPLE.COM:443/path?q=1#frag": "https://example.com/path?q=1",
                    "http://EXAMPLE.com.:80/": "http://example.com/",
                    "https://example.com:8443/a": "https://example.com:8443/a",
                    "https://8.8.8.8/": "https://8.8.8.8/",
                    "https://[2606:4700:4700::1111]/": "https://[2606:4700:4700::1111]/",
                    "https://不存在.invalid/a": "https://xn--ihqv5t4nd.invalid/a"}
        for raw, expected in accepted.items():
            with self.subTest(raw=raw):
                self.assertEqual(bookmark_import.canonicalize_url(raw), expected)
        response = self.upload("preview", html(*rejected))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["skipped"], len(rejected))

    def test_inert_malicious_html_and_persistence_duplicate_no_overwrite_and_all_sync(self):
        marker = Path(self.temp.name) / "executed"
        content = ('<!DOCTYPE NETSCAPE-Bookmark-file-1 [<!ENTITY xxe SYSTEM "file:///secret">]>'
                   '<script>fetch("https://evil.invalid");</script><img src="https://evil.invalid/image">'
                   f'{{{{ __import__("pathlib").Path({str(marker)!r}).touch() }}}}'
                   '<H3>Folder</H3><DL><A HREF="HTTPS://EXAMPLE.COM:443/page#one"><b>中文标题</b></A>'
                   '<A HREF="https://example.com/page#two">do not overwrite</A>'
                   '<A HREF="https://other.invalid/">Other</A><A HREF="javascript:evil()">bad</A></DL>')
        with patch("app.ai.service.ai_service.process_note", side_effect=AssertionError("AI forbidden")):
            first = self.upload("commit", content)
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual({key: value for key, value in body.items() if key != "note_ids"},
                         {"imported": 2, "duplicates": 1, "skipped": 1, "total": 4})
        self.assertEqual(len(body["note_ids"]), 2)
        second = self.upload("commit", content.replace("中文标题", "Changed")).json()
        self.assertEqual(second, {"imported": 0, "duplicates": 3, "skipped": 1, "total": 4, "note_ids": []})
        self.assertFalse(marker.exists())
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 2)
            note = db.get(Note, body["note_ids"][0])
            self.assertEqual(note.title, "中文标题")
            self.assertEqual(note.source, "bookmark")
            self.assertEqual(note.content, "https://example.com/page")
            self.assertEqual(note.source_url, note.content)
            self.assertEqual(note.source_id, hashlib.sha256(note.content.encode()).hexdigest())
            self.assertEqual(note.content_status, "link")
            self.assertEqual(note.ai_processed, 0)
            self.assertEqual(note.images, [])
            self.assertIsNone(note.category)
            self.assertEqual(db.query(DataSource).count(), 0)
        manifest = self.client.get("/api/sync/manifest").json()
        self.assertEqual([item["id"] for item in manifest["notes"]], body["note_ids"])
        batch = self.client.post("/api/sync/batch", json={"snapshot": manifest["snapshot"], "ids": body["note_ids"]})
        self.assertEqual(batch.status_code, 200, batch.text)
        self.assertEqual([note["source"] for note in batch.json()["notes"]], ["bookmark", "bookmark"])

    def test_limit_boundaries_title_truncation_and_1000_ids(self):
        thousand = html(*[f"https://example.com/{i}" for i in range(1000)])
        response = self.upload("commit", thousand)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 1000)
        self.assertEqual(len(response.json()["note_ids"]), 1000)
        for endpoint in ("preview", "commit"):
            response = self.upload(endpoint, html(*(["javascript:evil()"] * 1001)))
            self.assertEqual(response.status_code, 413)
        padded = html("https://boundary.invalid/")
        boundary = padded.encode() + b" " * (bookmark_import.MAX_FILE_BYTES - len(padded))
        self.assertEqual(self.upload("preview", boundary).status_code, 200)
        self.assertEqual(self.upload("preview", boundary + b"x").status_code, 413)
        title = self.upload("preview", '<a href="https://example.com/">' + "标" * 700 + '</a>').json()["items"][0]["title"]
        self.assertEqual(title, "标" * 500)
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 1000)

    def test_invalid_format_utf8_and_missing_file(self):
        cases = [(b"private-secret\xff", "bookmarks.html", 422),
                 (b"private-secret\x00", "bookmarks.html", 422),
                 (b"private-secret", "bookmarks.html", 422),
                 (b"", "bookmarks.html", 422), (b"  \r\n", "bookmarks.html", 422),
                 (html("https://example.com/"), "bookmarks.txt", 415)]
        for endpoint in ("preview", "commit"):
            for content, filename, code in cases:
                response = self.upload(endpoint, content, filename)
                self.assertEqual(response.status_code, code, response.text)
                self.assertNotIn("private-secret", response.text)
            self.assertEqual(self.client.post("/api/bookmark-import/" + endpoint).status_code, 422)
        self.assertEqual(self.upload("preview", "<!DOCTYPE NETSCAPE-Bookmark-file-1><DL></DL>").json()["total"], 0)
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 0)

    def test_stream_reads_are_bounded_and_closed(self):
        class Stream:
            filename = "bookmarks.html"
            size = 1
            consumed = 0
            closed = False
            async def read(self, size):
                if not 1 <= size <= 65536:
                    raise AssertionError("unbounded read")
                self.consumed += size
                return b"x" * size
            async def close(self):
                self.closed = True
        stream = Stream()
        with self.assertRaises(HTTPException) as error:
            asyncio.run(bookmark_import._read_bookmarks(stream))
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(stream.consumed, bookmark_import.MAX_FILE_BYTES + 1)
        self.assertTrue(stream.closed)

    def test_optional_preview_hash_binds_exact_upload_bytes(self):
        content = html("https://example.com/")
        digest = self.upload("preview", content).json()["content_hash"]
        mismatch = self.upload("commit", content + " ", data={"content_hash": digest})
        self.assertEqual(mismatch.status_code, 422)
        self.assertEqual(mismatch.json(), {"detail": "File does not match the preview; preview the file again"})
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 0)
        self.assertEqual(self.upload("commit", content, data={"content_hash": digest}).json()["imported"], 1)

    def test_database_failure_rolls_back_whole_batch_and_sanitizes(self):
        with patch("sqlalchemy.orm.Session.commit", side_effect=SQLAlchemyError("private-secret")):
            response = self.upload("commit", html("https://first.invalid/", "https://second.invalid/"))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Unable to import bookmarks"})
        with self.sessions() as db:
            self.assertEqual(db.query(Note).count(), 0)
        with patch("sqlalchemy.orm.Session.flush", side_effect=SQLAlchemyError("private-secret")), patch("sqlalchemy.orm.Session.rollback", autospec=True) as rollback:
            self.assertEqual(self.upload("commit", html("https://first.invalid/")).status_code, 500)
            rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
