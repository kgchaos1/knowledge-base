"""Offline regression suite: python -m unittest discover -s backend/tests -p test_zhihu_mock.py -v

Uses only unittest, declared backend dependencies, MockTransport and in-memory SQLite.
No real accounts, live server, external HTTP, or application database are used.
"""
import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Isolate app settings and database initialization from local .env / live engines.
_config = types.ModuleType("app.core.config")
_config.settings = types.SimpleNamespace(ZHIHU_COOKIE="")
_database = types.ModuleType("app.core.database")
_database.Base = declarative_base()
_backend = Path(__file__).resolve().parents[1]
_model_spec = importlib.util.spec_from_file_location("_zhihu_test_note", _backend / "app/models/note.py")
_model = importlib.util.module_from_spec(_model_spec)
with patch.dict(sys.modules, {"app.core.config": _config, "app.core.database": _database}):
    _model_spec.loader.exec_module(_model)
_spec = importlib.util.spec_from_file_location("_zhihu_under_test", _backend / "app/importers/zhihu.py")
zhihu = importlib.util.module_from_spec(_spec)
with patch.dict(sys.modules, {"app.core.config": _config, "app.models.note": _model}):
    _spec.loader.exec_module(zhihu)
Note = zhihu.Note
_membership_spec = importlib.util.spec_from_file_location("_zhihu_test_membership", _backend / "app/models_membership.py")
_membership = importlib.util.module_from_spec(_membership_spec)
with patch.dict(sys.modules, {"app.core.database": _database}):
    _membership_spec.loader.exec_module(_membership)
NoteSource = _membership.NoteSource
_REAL_CLIENT = httpx.AsyncClient


def item(kind="answer", item_id=1, **fields):
    content = dict(type=kind, id=item_id, content="<p>Full <b>body</b></p>",
                   excerpt="short", question={"id": 10, "title": "Question"},
                   author={"name": "Author"}, title="Article")
    content.update(fields)
    return {"content": content}


def page(items=None, end=True):
    return {"data": items or [], "paging": {"is_end": end}}


class ZhihuMockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        _database.Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.config = {"collection_url": "123", "cookie": "SECRET_COOKIE"}
        self.requests = []

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    async def run_import(self, replies, config=None):
        def handler(request):
            self.requests.append(request)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

        transport = httpx.MockTransport(handler)
        def client(**kwargs):
            return _REAL_CLIENT(transport=transport, **kwargs)
        with patch.dict(sys.modules, {"app.models_membership": _membership}), patch.object(zhihu.httpx, "AsyncClient", side_effect=client), patch.object(zhihu.asyncio, "sleep", new_callable=AsyncMock):
            return await zhihu.import_from_zhihu(config or self.config, self.db)

    async def test_full_body_html_plain_text_source_url_and_typed_ids(self):
        result = await self.run_import([page([item(), item("article", 1)])])
        self.assertEqual(result["success"], 2)
        self.assertEqual(result["status"], "completed")
        notes = self.db.query(Note).order_by(Note.id).all()
        self.assertEqual([n.source_id for n in notes], ["answer:1", "article:1"])
        self.assertIn("Full", notes[0].content)
        self.assertIn("body", notes[0].content)
        self.assertNotIn("short", notes[0].content)
        self.assertNotIn("<", notes[0].content)
        self.assertEqual(notes[0].source_url, "https://www.zhihu.com/question/10/answer/1")
        self.assertEqual(notes[1].source_url, "https://zhuanlan.zhihu.com/p/1")

    async def test_excerpt_fallback_empty_and_unsupported_are_skipped(self):
        result = await self.run_import([page([
            item(content="<p> </p>", excerpt="<p>Fallback &amp; text</p>"),
            item(item_id=2, content="<script>secret</script>", excerpt=" "),
            item(kind="video", item_id=3),
        ])])
        self.assertEqual((result["success"], result["skipped"], result["failed"]), (1, 2, 0))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.db.query(Note).one().content, "Fallback & text")

    async def test_numeric_id_and_approved_urls_only(self):
        for value in [None, "", "abc", "１２３", "1/2", " 123", "https://www.zhihu.com/collection/1\n", True, -1, 1.5,
                      "https://evil.test/collection/123", "https://www.zhihu.com.evil.test/collection/1",
                      "https://user@www.zhihu.com/collection/1", "https://www.zhihu.com/collection/1/extra"]:
            with self.subTest(value=value), patch.object(zhihu.httpx, "AsyncClient") as client:
                with self.assertRaises(ValueError):
                    await zhihu.import_from_zhihu(dict(self.config, collection_url=value), self.db)
                client.assert_not_called()
        for value in [123, "123", "https://www.zhihu.com/collection/123/?foo=bar#fragment"]:
            result = await self.run_import([page()], dict(self.config, collection_url=value))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.requests[-1].url.path, "/api/v4/collections/123/items")

    async def test_http_errors_are_explicit_and_redacted(self):
        for code in [301, 401, 403, 404, 429, 500]:
            with self.subTest(code=code):
                with self.assertRaisesRegex(RuntimeError, f"HTTP {code}") as error:
                    await self.run_import([httpx.Response(code, text="SECRET_COOKIE private response")])
                self.assertNotIn("SECRET", str(error.exception))
                self.assertNotIn("private", str(error.exception))
        self.assertEqual(len(self.requests), 6)

    async def test_timeout_and_network_error_are_redacted(self):
        for exception in [httpx.ReadTimeout("SECRET_COOKIE"), httpx.ConnectError("SECRET_COOKIE")]:
            with self.subTest(exception=type(exception).__name__):
                with self.assertRaises(RuntimeError) as error:
                    await self.run_import([exception])
                self.assertNotIn("SECRET", str(error.exception))

    async def test_invalid_json_and_schema_raise(self):
        invalid = [httpx.Response(200, text="SECRET_COOKIE invalid json"), [], {},
                   {"data": [], "paging": {"is_end": "true"}},
                   {"data": {}, "paging": {"is_end": True}},
                   page([{"content": []}]), page([None]), page([], end=False)]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                await self.run_import([payload])

    async def test_later_page_error_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            await self.run_import([page([item()], end=False), httpx.Response(403)])
        self.assertEqual(self.db.query(Note).count(), 1)

    async def test_pagination_is_bounded_and_reports_truncation(self):
        replies = [page([item(item_id=p * 20 + i) for i in range(20)], end=False) for p in range(5)]
        result = await self.run_import(replies)
        self.assertEqual((result["total"], result["success"]), (100, 100))
        self.assertEqual(len(self.requests), 5)
        self.assertEqual([r.url.params["offset"] for r in self.requests], ["0", "20", "40", "60", "80"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["status"], "partial")
        self.assertIn("Truncated", result["message"])

    async def test_oversized_page_cannot_exceed_item_budget(self):
        result = await self.run_import([page([item(item_id=i) for i in range(150)])])
        self.assertEqual(result["total"], 20)
        self.assertTrue(result["truncated"])

    async def test_repeated_import_is_idempotent_and_duplicates_not_success(self):
        await self.run_import([page([item()])])
        result = await self.run_import([page([item(), item()])])
        self.assertEqual(result["success"], 0)
        self.assertEqual(result["duplicates"], 2)
        self.assertEqual(self.db.query(Note).count(), 1)

    async def test_legacy_numeric_id_requires_matching_type_and_url(self):
        self.db.add_all([
            Note(title="legacy answer", content="old", source="zhihu", source_id="1",
                 source_url="https://www.zhihu.com/question/10/answer/1?old=1"),
            Note(title="legacy article", content="old", source="zhihu", source_id="2",
                 source_url="https://zhuanlan.zhihu.com/p/2"),
            Note(title="unknown legacy", content="old", source="zhihu", source_id="3", source_url=None),
        ])
        self.db.commit()
        result = await self.run_import([page([item(), item("article", 1), item("article", 2), item(item_id=3)])])
        self.assertEqual((result["success"], result["duplicates"]), (2, 2))
        self.assertEqual(self.db.query(Note).count(), 5)
        self.assertEqual(self.db.query(Note).filter(Note.source_id == "article:1").count(), 1)

    async def test_source_membership_new_existing_legacy_and_repeat_deduplication(self):
        legacy = Note(title="legacy", content="old", source="zhihu", source_id="2",
                      source_url="https://www.zhihu.com/answer/2")
        self.db.add(legacy)
        self.db.commit()
        first = await self.run_import([page([item(), item(item_id=2), item()])],
                                      dict(self.config, _source_id=7))
        self.assertEqual((first["success"], first["duplicates"], first["failed"]), (1, 2, 0))
        await self.run_import([page([item(), item(item_id=2)])], dict(self.config, _source_id=7))
        await self.run_import([page([item()])], dict(self.config, _source_id=8))
        self.assertEqual(self.db.query(Note).count(), 2)
        pairs = {(link.source_id, link.note_id) for link in self.db.query(NoteSource)}
        new_note = self.db.query(Note).filter(Note.source_id == "answer:1").one()
        self.assertEqual(pairs, {(7, legacy.id), (7, new_note.id), (8, new_note.id)})

    async def test_source_membership_not_inferred_without_trusted_id(self):
        await self.run_import([page([item()])])
        self.assertEqual(self.db.query(NoteSource).count(), 0)
        for invalid in (True, "7", 7.0, 0, -1):
            with self.assertRaises(ValueError):
                await self.run_import([], dict(self.config, _source_id=invalid))

    async def test_invalid_item_id_is_a_failure_not_success(self):
        result = await self.run_import([page([item(item_id=None)])])
        self.assertEqual((result["success"], result["failed"]), (0, 1))
        self.assertEqual(result["status"], "partial")

    async def test_missing_cookie_prevents_network_access(self):
        with patch.object(zhihu.settings, "ZHIHU_COOKIE", ""), patch.object(zhihu.httpx, "AsyncClient") as client:
            with self.assertRaisesRegex(ValueError, "cookie"):
                await zhihu.import_from_zhihu({"collection_url": "123"}, self.db)
            client.assert_not_called()

    async def test_invalid_cookie_header_is_rejected_without_echo(self):
        for cookie in ["SECRET_COOKIE\n", "SECRET_COOKIE中文"]:
            with patch.object(zhihu.httpx, "AsyncClient") as client:
                with self.assertRaises(ValueError) as error:
                    await zhihu.import_from_zhihu(dict(self.config, cookie=cookie), self.db)
                client.assert_not_called()
                self.assertNotIn("SECRET", str(error.exception))

    async def test_untrusted_paging_url_is_never_followed(self):
        first = page([item()], end=False)
        first["paging"]["next"] = "https://evil.test/steal"
        await self.run_import([first, page([item(item_id=2)])])
        self.assertTrue(all(request.url.host == "www.zhihu.com" for request in self.requests))

    async def test_malformed_body_is_a_failure_and_not_a_success(self):
        result = await self.run_import([page([item(content={"unexpected": "format"})])])
        self.assertEqual((result["success"], result["failed"]), (0, 1))
        self.assertEqual(result["status"], "partial")

    async def test_database_failure_is_counted_and_redacted(self):
        with patch.object(self.db, "commit", side_effect=RuntimeError("SECRET_COOKIE")), self.assertLogs(zhihu.logger, level="WARNING") as logs:
            result = await self.run_import([page([item()])])
        self.assertEqual((result["success"], result["failed"]), (0, 1))
        self.assertEqual(self.db.query(Note).count(), 0)
        self.assertNotIn("SECRET", " ".join(logs.output))

    async def test_final_page_at_limit_is_not_truncated(self):
        replies = [page([item(item_id=i)], end=i == 4) for i in range(5)]
        result = await self.run_import(replies)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["status"], "completed")

    async def test_untrusted_source_link_gets_canonical_fallback(self):
        await self.run_import([page([item("article", 1, url="https://evil.test/p/1")])])
        self.assertEqual(self.db.query(Note).one().source_url, "https://zhuanlan.zhihu.com/p/1")

    async def test_concurrent_enrichment_rechecks_identity(self):
        entered = 0
        ready = asyncio.Event()
        async def enrich(*args, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            return dict(text='body', completeness='full', images=[], image_total=0,
                        image_failed=0, warning='')
        module = types.ModuleType('app.importers.zhihu_content')
        module.enrich_content = enrich
        def client(**kwargs):
            return _REAL_CLIENT(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=page([item()]))), **kwargs)
        with sessionmaker(bind=self.engine)() as other, patch.dict(sys.modules, {'app.importers.zhihu_content': module}), patch.object(zhihu.httpx, 'AsyncClient', side_effect=client):
            results = await asyncio.gather(
                zhihu.import_from_zhihu(dict(self.config, _enrich=True), self.db),
                zhihu.import_from_zhihu(dict(self.config, collection_url='456', _enrich=True), other))
        self.assertEqual(self.db.query(Note).count(), 1)
        self.assertEqual(sum(r['success'] for r in results), 1)
        self.assertEqual(sum(r['duplicates'] for r in results), 1)

    async def test_scheduler_threads_serialize_identity_commit(self):
        gate = threading.Barrier(2)
        module = types.ModuleType('app.importers.zhihu_content')
        async def enrich(*args, **kwargs):
            gate.wait(timeout=5)
            return dict(text='thread body', completeness='full', images=[],
                        image_total=0, image_failed=0, warning='')
        module.enrich_content = enrich
        def client(**kwargs):
            return _REAL_CLIENT(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=page([item()]))), **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine('sqlite:///' + str(Path(directory) / 'race.db'),
                                   connect_args={'check_same_thread': False})
            _database.Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            def worker(collection):
                with sessions() as db:
                    return asyncio.run(zhihu.import_from_zhihu(
                        dict(self.config, collection_url=collection, _enrich=True), db))
            try:
                with patch.dict(sys.modules, {'app.importers.zhihu_content': module}), patch.object(zhihu.httpx, 'AsyncClient', side_effect=client):
                    results = await asyncio.gather(asyncio.to_thread(worker, '123'),
                                                   asyncio.to_thread(worker, '456'))
                with sessions() as db:
                    self.assertEqual(db.query(Note).count(), 1)
                self.assertEqual(sum(r['success'] for r in results), 1)
                self.assertEqual(sum(r['duplicates'] for r in results), 1)
                self.assertEqual(sum(r['failed'] for r in results), 0)
            finally:
                engine.dispose()

    async def test_enriched_image_only_is_saved_without_ai_candidate(self):
        module = types.ModuleType('app.importers.zhihu_content')
        image = {'path': 'a' * 64 + '.png', 'url': 'https://a.zhimg.com/a'}
        module.enrich_content = AsyncMock(return_value=dict(text='', completeness='full',
            images=[image], image_total=1, image_failed=0, warning=''))
        with patch.dict(sys.modules, {'app.importers.zhihu_content': module}):
            result = await self.run_import([page([item()])], dict(self.config, _enrich=True))
        self.assertEqual((result['success'], result['skipped']), (1, 0))
        self.assertEqual(result['changed_note_ids'], [])
        note = self.db.query(Note).one()
        self.assertEqual((note.content, note.content_status, note.images), ('', 'full', [image]))

    async def test_partial_images_preserve_previous_local_references_and_full_body(self):
        images = [{'path': char * 64 + '.png', 'url': 'https://a.zhimg.com/' + char}
                  for char in 'abc']
        self.db.add(Note(title='old', source='zhihu', source_id='answer:1',
            content='Original full body', content_status='full', images=images[:2]))
        self.db.commit()
        module = types.ModuleType('app.importers.zhihu_content')
        module.enrich_content = AsyncMock(return_value=dict(text='short', completeness='excerpt',
            images=[images[0], images[2]], image_total=3, image_failed=1, warning='One image unavailable.'))
        with patch.dict(sys.modules, {'app.importers.zhihu_content': module}):
            result = await self.run_import([page([item()])], dict(self.config, _enrich=True))
        note = self.db.query(Note).one()
        self.assertEqual(note.content, 'Original full body')
        self.assertEqual(note.content_status, 'full')
        self.assertEqual({i['path'] for i in note.images}, {i['path'] for i in images})
        self.assertEqual((note.image_total, note.image_failed), (3, 1))
        self.assertIn('Previous local images retained', note.content_warning)
        self.assertEqual(result['updated'], 0)

    async def test_manual_signature_and_skipped_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual.txt"
            path.write_text("Title\nA long enough body\n---\nshort", encoding="utf-8")
            result = await zhihu.import_from_zhihu_manual(str(path), self.db)
        self.assertEqual(result, {"total": 2, "success": 1, "failed": 0, "skipped": 1})


if __name__ == "__main__":
    unittest.main()
