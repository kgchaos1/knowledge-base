"""Offline enrichment/security tests: python -m unittest discover -s backend/tests -p test_zhihu_content.py -v"""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

_spec = importlib.util.spec_from_file_location('zhihu_content_test_target',
    Path(__file__).resolve().parents[1] / 'app/importers/zhihu_content.py')
zc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(zc)
PNG = b'\x89PNG\r\n\x1a\n' + b'payload'


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class EnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, detail, fallback='', images=None, dns=None, kind='answer', **kwargs):
        self.requests = []
        self.dns = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        def handler(request):
            self.requests.append(request)
            if request.url.host == 'www.zhihu.com':
                if isinstance(detail, Exception):
                    raise detail
                return detail if isinstance(detail, httpx.Response) else httpx.Response(200, json=detail)
            return (images or {}).get(str(request.url), httpx.Response(200, content=PNG))
        async def resolver(host):
            self.dns.append(host)
            return dns if dns is not None else ['8.8.8.8', '2606:4700:4700::1111']
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                headers={'Cookie': 'DEFAULT_COOKIE', 'Authorization': 'Bearer DEFAULT_AUTH'},
                cookies={'ambient': 'secret'}, auth=('user', 'password'), follow_redirects=True) as client:
            result = await zc.enrich_content(kind, '12', fallback, 'DETAIL_SECRET',
                client=client, attachment_dir=self.directory, resolver=resolver, **kwargs)
            self.assertFalse(client.is_closed)
        return result

    async def test_detail_plain_text_order_precedence_credentials_and_storage(self):
        result = await self.run_case({'content': '<script>secret</script><style>secret</style><p>Full &amp; body</p>'
            '<img data-original="https://a.zhimg.com/1" data-actualsrc="https://a.zhimg.com/no" src="https://a.zhimg.com/no2" alt="一">'
            '<img data-actualsrc="https://a.zhimg.com/2" src="https://a.zhimg.com/no3">'
            '<img src="https://a.zhimg.com/3">'}, fallback='excerpt', kind='article')
        self.assertEqual(result['text'], 'Full & body')
        self.assertEqual(result['completeness'], 'full')
        self.assertEqual(result['warning'], '')
        self.assertEqual(result['image_total'], 3)
        self.assertEqual([i['url'] for i in result['images']], [f'https://a.zhimg.com/{n}' for n in (1, 2, 3)])
        self.assertEqual(result['images'][0]['alt'], '一')
        filename = hashlib.sha256(PNG).hexdigest() + '.png'
        self.assertEqual(result['images'][0]['path'], filename)
        self.assertEqual((self.directory / filename).read_bytes(), PNG)
        self.assertEqual(len(list(self.directory.iterdir())), 1)
        self.assertEqual(self.requests[0].url.path, '/api/v4/articles/12')
        self.assertEqual(self.requests[0].headers['cookie'], 'DETAIL_SECRET')
        for request in self.requests:
            self.assertNotIn('authorization', request.headers)
        for request in self.requests[1:]:
            self.assertNotIn('cookie', request.headers)

    async def test_fallback_http_redirect_schema_empty_and_network_failures(self):
        for detail in [httpx.Response(302, headers={'Location': 'https://evil.test/steal'}),
                       httpx.Response(403), httpx.Response(200, text='SECRET not JSON'),
                       {}, [], {'content': 12}, {'content': '<script>invisible</script>'},
                       {'content': '<img src="https://evil.test/x">'},
                       httpx.ReadTimeout('SECRET')]:
            with self.subTest(detail=detail):
                result = await self.run_case(detail, '<p>Original &amp; safe</p>')
                self.assertEqual(result['text'], 'Original & safe')
                self.assertEqual(result['completeness'], 'excerpt')
                self.assertNotIn('SECRET', result['warning'])
                self.assertEqual(len(self.requests), 1)
        result = await self.run_case({}, '')
        self.assertEqual(result['completeness'], 'link')
        result = await self.run_case({'excerpt': 'not content'}, '<img src="https://a.zhimg.com/x">')
        self.assertEqual(result['completeness'], 'excerpt')
        self.assertEqual(len(result['images']), 1)

    async def test_image_only_detail_can_be_full(self):
        result = await self.run_case({'content': '<img src="https://a.zhimg.com/x">'})
        self.assertEqual(result['completeness'], 'full')
        self.assertEqual(result['text'], '')

    async def test_strict_url_allowlist_rejects_before_dns_or_http(self):
        urls = ['http://a.zhimg.com/x', 'https://zhimg.com/x', 'https://a.zhimg.com.evil.test/x',
                'https://a.zhimg.com:443/x', 'https://a.zhimg.com:/x',
                'https://user@a.zhimg.com/x', 'https://u:p@a.zhimg.com/x',
                'https://127.0.0.1/x', 'https://a.zhimg.com./x', 'https://a.zhimg.com/x#fragment',
                '//a.zhimg.com/x', 'data:image/png;base64,xx', 'https://a.zhimg.com\\@evil.test/x',
                'https://bad_label.zhimg.com/x', 'https://-bad.zhimg.com/x', 'https://a.zhimg.com/ x']
        result = await self.run_case({'content': 'Body' + ''.join(f'<img src="{url}">' for url in urls)})
        self.assertEqual(result['image_failed'], len(urls))
        self.assertEqual(self.dns, [])
        self.assertEqual(len(self.requests), 1)

    async def test_dns_all_answers_must_be_public(self):
        for dns in [[], ['127.0.0.1'], ['10.1.2.3'], ['192.168.0.1'], ['169.254.169.254'],
                    ['0.0.0.0'], ['224.0.0.1'], ['ff02::1'], ['::1'], ['fc00::1'], ['fe80::1'], ['::ffff:127.0.0.1'],
                    ['8.8.8.8', '172.16.0.1']]:
            with self.subTest(dns=dns):
                result = await self.run_case({'content': 'Body<img src="https://a.zhimg.com/x">'}, dns=dns)
                self.assertEqual(result['image_failed'], 1)
                self.assertEqual(len(self.requests), 1)

    async def test_images_redirect_svg_and_unrecognized_magic_rejected(self):
        for response in [httpx.Response(302, headers={'Location': 'https://a.zhimg.com/next'}),
                         httpx.Response(200, content=b'<svg xmlns="x"/>', headers={'Content-Type': 'image/png'}),
                         httpx.Response(200, content=b'not image'), httpx.Response(404)]:
            result = await self.run_case({'content': 'Body<img src="https://a.zhimg.com/x">'},
                                         images={'https://a.zhimg.com/x': response})
            self.assertEqual(result['image_failed'], 1)
            self.assertEqual(result['completeness'], 'full')
            self.assertEqual(len(self.requests), 2)
            self.assertEqual(list(self.directory.iterdir()), [])

    async def test_supported_magic_and_no_extension_trust(self):
        for data, extension in [(PNG, 'png'), (b'\xff\xd8\xffrest', 'jpg'),
                                (b'GIF87arest', 'gif'), (b'GIF89arest', 'gif'),
                                (b'RIFF1234WEBPrest', 'webp')]:
            result = await self.run_case({'content': '<img src="https://a.zhimg.com/evil.svg">'},
                images={'https://a.zhimg.com/evil.svg': httpx.Response(200, content=data)})
            self.assertTrue(result['images'][0]['path'].endswith('.' + extension))

    async def test_image_count_limit_and_failed_occurrences(self):
        result = await self.run_case({'content': 'Body' + '<img src="https://a.zhimg.com/x">' * 23})
        self.assertEqual((result['image_total'], result['image_failed']), (23, 3))
        self.assertEqual(len(result['images']), 20)
        self.assertEqual(len(self.requests), 21)

    async def test_stream_limits_close_and_cleanup(self):
        stream = ChunkStream([PNG, b'x' * 80])
        with patch.object(zc, 'MAX_IMAGE_BYTES', 40):
            result = await self.run_case({'content': 'Body<img src="https://a.zhimg.com/x">'},
                images={'https://a.zhimg.com/x': httpx.Response(200, stream=stream)})
        self.assertEqual(result['image_failed'], 1)
        self.assertTrue(stream.closed)
        self.assertEqual(list(self.directory.iterdir()), [])
        with patch.object(zc, 'MAX_TOTAL_BYTES', len(PNG) * 2):
            result = await self.run_case({'content': 'Body' + '<img src="https://a.zhimg.com/x">' * 4})
        self.assertEqual(len(result['images']), 2)
        self.assertEqual(result['image_failed'], 2)
        self.assertEqual(len(self.requests), 3)

    async def test_failed_magic_consumes_total_budget(self):
        with patch.object(zc, 'MAX_TOTAL_BYTES', 10):
            result = await self.run_case({'content': 'Body' + '<img src="https://a.zhimg.com/x">' * 3},
                images={'https://a.zhimg.com/x': httpx.Response(200, content=b'1234567890')})
        self.assertEqual(result['image_failed'], 3)
        self.assertEqual(len(self.requests), 2)

    async def test_invalid_identity_and_cookie_before_network(self):
        for kind, ident, cookie in [('video', '1', ''), ('answer', True, ''), ('answer', '１２', ''),
                                     ('answer', '../x', ''), ('answer', '1', 'SECRET\n'),
                                     ('answer', '1', '中文')]:
            with patch.object(zc.httpx, 'AsyncClient') as client:
                with self.assertRaises(ValueError) as error:
                    await zc.enrich_content(kind, ident, '', cookie)
                self.assertNotIn('SECRET', str(error.exception))
                client.assert_not_called()

    async def test_content_addressed_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            filename = zc._save(PNG, path)
            before = (path / filename).stat().st_mtime_ns
            self.assertEqual(zc._save(PNG, path), filename)
            self.assertEqual((path / filename).stat().st_mtime_ns, before)
            (path / filename).write_bytes(b'existing conflict')
            with self.assertRaises(ValueError):
                zc._save(PNG, path)
            self.assertEqual((path / filename).read_bytes(), b'existing conflict')
            self.assertEqual(len(list(path.iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
