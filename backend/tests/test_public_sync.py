"""Explicit public testing mode remains narrow and reversible."""
import unittest
from fastapi.testclient import TestClient
from test_auth import make_app, ORIGIN


class PublicSyncTests(unittest.TestCase):
    def client(self, **overrides):
        app = make_app(**overrides)
        @app.get('/api/sync/manifest')
        @app.get('/api/sync/sources')
        @app.post('/api/sync/batch')
        @app.get('/api/attachments/' + 'a' * 64 + '.png')
        def sync():
            return {'ok': True}
        return TestClient(app, base_url=ORIGIN)

    def test_default_off_and_explicit_true_only(self):
        for flag in [False, 'true', 1, None]:
            with self.client(PKM_TEST_PUBLIC_SYNC=flag) as c:
                self.assertEqual(c.get('/api/sync/manifest').status_code, 401)

    def test_public_read_only_routes(self):
        with self.client(PKM_TEST_PUBLIC_SYNC=True) as c:
            for path in ['/api/sync/manifest', '/api/sync/sources', '/api/attachments/'+'a'*64+'.png']:
                r = c.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers['cache-control'], 'no-store')
                self.assertEqual(r.headers['x-pkm-test-public-sync'], 'true')
            self.assertEqual(c.post('/api/sync/batch', headers={'Origin': ORIGIN}, json={}).status_code, 200)
            self.assertEqual(c.post('/api/sync/batch', json={}).status_code, 403)
            self.assertEqual(c.post('/api/sync/batch', headers={'Origin': 'https://evil.test'}).status_code, 403)

    def test_admin_and_other_paths_still_protected(self):
        with self.client(PKM_TEST_PUBLIC_SYNC=True) as c:
            for path in ['/api/notes/', '/api/sources/', '/api/auth/me', '/api/devices', '/docs', '/api/sync/manifest/', '/api/attachments/unsafe.png']:
                self.assertEqual(c.get(path).status_code, 401, path)
            for method, path in [('POST', '/api/notes/'), ('DELETE', '/api/notes/'), ('POST', '/api/sync/manifest'), ('POST', '/api/devices/pairing')]:
                self.assertEqual(c.request(method, path, headers={'Origin': ORIGIN}).status_code, 401)
            # Invalid credentials must not silently become anonymous.
            self.assertEqual(c.get('/api/sync/manifest', headers={'Authorization': 'invalid'}).status_code, 403)

    def test_transport_and_configuration_remain_required(self):
        with self.client(PKM_TEST_PUBLIC_SYNC=True, SECRET_KEY='') as c:
            self.assertEqual(c.get('/api/sync/manifest').status_code, 503)
        with self.client(PKM_TEST_PUBLIC_SYNC=True) as c:
            self.assertEqual(c.get('http://pkm.test/api/sync/manifest').status_code, 403)


if __name__ == '__main__':
    unittest.main()
