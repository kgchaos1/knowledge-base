"""Offline tests: loopback HTTP only; all filesystem writes use TemporaryDirectory."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import zipfile

from backup_client import (BackupClient, BackupError, Cancelled, HTTP_WARNING,
                           load_config, main, run_watch, safe_filename, save_config)


def zip_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("data.txt", "offline backup")
    return stream.getvalue()


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = {}
        state = cls.state
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, code, body=b"", cookie=False):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                if cookie:
                    self.send_header("Set-Cookie", "pkm=session; Path=/; HttpOnly")
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                state["logins"] += 1
                state["login_path"] = self.path
                state["origin"] = self.headers.get("Origin")
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["password"] = body.get("password")
                self.reply(200 if body == {"password": "secret"} else 401, b"{}", True)
            def do_GET(self):
                if self.headers.get("Cookie") != "pkm=session":
                    self.reply(401)
                    return
                if state.get("expire"):
                    state["expire"] = False
                    self.reply(401)
                    return
                if self.path == "/api/backups/":
                    if state.get("list_fail"):
                        self.reply(503)
                    else:
                        self.reply(200, json.dumps(state["items"]).encode())
                else:
                    state["downloads"].append(self.path)
                    if state.get("download_fail"):
                        self.reply(503)
                    else:
                        self.reply(200, state["payload"])
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%d" % cls.httpd.server_port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "backups"
        self.payload = zip_bytes()
        self.item = dict(id=42, filename="backup.zip", size=len(self.payload),
                         sha256=hashlib.sha256(self.payload).hexdigest(),
                         created_at="2026-11-04T01:00:00Z", status="completed")
        self.state.clear()
        self.state.update(items=[dict(self.item)], payload=self.payload, logins=0, downloads=[])
        self.client = BackupClient(self.url, self.directory, "secret", allow_http=True, timeout=2)

    def test_http_requires_confirmation(self):
        with self.assertRaises(BackupError):
            BackupClient(self.url, self.directory, "secret")
        with patch("builtins.input", return_value="no") as confirm, patch("getpass.getpass") as password:
            self.assertEqual(main(["--once", "--server", self.url, "--directory", str(self.directory)]), 1)
            self.assertIn(HTTP_WARNING, confirm.call_args.args[0])
            password.assert_not_called()
        self.assertEqual(self.state["logins"], 0)

    def test_download_cookie_origin_integrity_no_extract_and_idempotent(self):
        result = self.client.sync_once()
        self.assertEqual(result.read_bytes(), self.payload)
        self.assertEqual(self.state["origin"], self.url)
        self.assertEqual(self.state["login_path"], "/api/auth/login")
        self.assertEqual(self.state["password"], "secret")
        self.assertFalse((self.directory / "data.txt").exists())
        self.assertIsNone(self.client.sync_once())
        self.assertEqual(self.state["downloads"], ["/api/backups/42/download"])
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_only_latest_completed_not_downloaded(self):
        old = dict(self.item, id=1, filename="old.zip", created_at="2026-01-01T00:00:00+00:00")
        future = dict(self.item, id=3, filename="pending.zip", created_at="2027-01-01T00:00:00Z", status="pending")
        self.state["items"] = [old, future, self.item]
        self.assertEqual(self.client.sync_once().name, "backup.zip")
        self.assertFalse((self.directory / "old.zip").exists())
        self.assertEqual(self.client.sync_once().name, "old.zip")
        self.assertIsNone(self.client.sync_once())

    def test_expired_cookie_relogin_once(self):
        self.state["expire"] = True
        self.client.sync_once()
        self.assertEqual(self.state["logins"], 2)

    def test_wrong_password_fails_without_loop(self):
        self.client.password = "wrong"
        with self.assertRaises(Exception):
            self.client.sync_once()
        self.assertEqual(self.state["logins"], 1)

    def test_network_http_failure_not_recorded_and_retry_works(self):
        self.state["download_fail"] = True
        with self.assertRaises(Exception):
            self.client.sync_once()
        self.assertEqual(list(self.directory.iterdir()), [])
        self.state["download_fail"] = False
        self.assertTrue(self.client.sync_once().is_file())

    def test_transport_failure_cleans_partial_and_retries(self):
        original = self.client.request
        from urllib.error import URLError
        def interrupted(path):
            if path.endswith("/download"):
                raise URLError("offline")
            return original(path)
        with patch.object(self.client, "request", side_effect=interrupted):
            with self.assertRaises(URLError):
                self.client.sync_once()
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertTrue(self.client.sync_once().is_file())

    def test_zip_not_visible_until_verification(self):
        original = self.client.request
        target = self.directory / "backup.zip"
        observed = []
        payload = self.payload
        class Response(io.BytesIO):
            def read(self, size=-1):
                observed.append(target.exists())
                return super().read(size)
        def streaming(path):
            return Response(payload) if path.endswith("/download") else original(path)
        with patch.object(self.client, "request", side_effect=streaming):
            self.client.sync_once()
        self.assertTrue(observed)
        self.assertFalse(any(observed))
        self.assertTrue(target.is_file())

    def test_list_failure_no_success(self):
        self.state["list_fail"] = True
        with self.assertRaises(Exception):
            self.client.sync_once()
        self.assertFalse(self.directory.exists())

    def test_size_and_digest_failures(self):
        for updates in ({"size": len(self.payload) + 1}, {"size": len(self.payload) - 1}, {"sha256": "0" * 64}):
            with self.subTest(updates=updates):
                self.state["items"] = [dict(self.item, **updates)]
                with self.assertRaises(BackupError):
                    self.client.sync_once()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_unsafe_filename_rejected_before_download(self):
        for name in ("../x.zip", "..\\x.zip", "/tmp/x.zip", "C:\\x.zip", "x:ads.zip", "CON.zip",
                     "COM1.zip", "NUL.zip", "a\x00.zip", "a\n.zip", "x.zip ", " x.zip", "x.tar", "a?.zip"):
            with self.subTest(name=name):
                self.state["items"] = [dict(self.item, filename=name)]
                with self.assertRaises(BackupError):
                    self.client.sync_once()
        self.assertEqual(self.state["downloads"], [])
        self.assertEqual(safe_filename("中文备份-01.zip"), "中文备份-01.zip")

    def test_invalid_metadata(self):
        for change in ({"size": True}, {"size": -1}, {"sha256": "bad"}, {"created_at": "2026-01-01"}, {"id": None}):
            self.state["items"] = [dict(self.item, **change)]
            with self.assertRaises(BackupError):
                self.client.sync_once()
        self.state["items"] = {"items": []}
        with self.assertRaises(BackupError):
            self.client.sync_once()

    def test_existing_file_never_deleted_or_overwritten(self):
        self.directory.mkdir()
        target = self.directory / "backup.zip"
        target.write_bytes(b"precious original")
        with self.assertRaises(BackupError):
            self.client.sync_once()
        self.assertEqual(target.read_bytes(), b"precious original")
        self.assertEqual(self.state["downloads"], [])

    def test_atomic_no_clobber_race(self):
        self.directory.mkdir()
        target = self.directory / "backup.zip"
        def race(source, destination):
            target.write_bytes(b"concurrent original")
            raise FileExistsError()
        with patch("backup_client.os.link", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.client.sync_once()
        self.assertEqual(target.read_bytes(), b"concurrent original")
        self.assertEqual(list(self.directory.glob("*.part")), [])

    def test_stop_before_network(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            self.client.sync_once(stop)
        self.assertEqual(self.state["logins"], 0)

    def test_watch_failures_wait_and_retry_without_busy_poll(self):
        class Stop:
            waits = []
            def is_set(self):
                return False
            def wait(self, seconds):
                self.waits.append(seconds)
                return len(self.waits) == 2
        stop = Stop()
        messages = []
        with patch.object(self.client, "sync_once", side_effect=[OSError("offline"), None]) as sync:
            run_watch(self.client, 2, stop, messages.append)
        self.assertEqual(sync.call_count, 2)
        self.assertEqual(stop.waits, [120, 120])
        self.assertEqual(len(messages), 2)

    def test_configuration_contains_only_allowed_values(self):
        config = Path(self.temp.name) / "settings" / "config.json"
        save_config(self.url, self.directory, 5, config)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"server", "dir", "interval"})
        self.assertNotIn("secret", config.read_text(encoding="utf-8"))
        self.assertEqual(load_config(config), data)
        config.write_text('{"password":"secret","server":"https://example.com"}', encoding="utf-8")
        self.assertEqual(load_config(config), {"server": "https://example.com"})

    def test_redirect_refused(self):
        from backup_client import NoRedirect
        with self.assertRaises(BackupError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.com")


if __name__ == "__main__":
    unittest.main()
