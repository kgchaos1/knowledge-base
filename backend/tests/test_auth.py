"""Offline: .venv-test/Scripts/python -m unittest discover -s backend/tests -p test_auth.py -v.

Auth tests use a fresh FastAPI and explicit config, never global .env/settings.
The real-app wiring check runs in a sanitized subprocess and temporary cwd.
"""
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.auth import AuthState, COOKIE_NAME, LoginLimiter, hash_password, install_auth

ORIGIN = "https://pkm.test"
PASSWORD = "correct horse battery staple"
HASH = hash_password(PASSWORD)


def config(**overrides):
    values = dict(PKM_PASSWORD_HASH=HASH, SECRET_KEY="a" * 48,
                  PKM_PUBLIC_ORIGIN=ORIGIN, PKM_SESSION_TTL_SECONDS=60,
                  PKM_LOGIN_MAX_ATTEMPTS=100, PKM_LOGIN_WINDOW_SECONDS=60,
                  PKM_TRUSTED_PROXY_PEERS="")
    values.update(overrides)
    return SimpleNamespace(**values)


def make_app(**overrides):
    app = FastAPI()
    install_auth(app, config(**overrides))
    @app.get("/api/health")
    def health():
        return {"status": "healthy"}
    @app.api_route("/api/notes/", methods=["GET", "POST", "PATCH", "DELETE"])
    @app.get("/api/ai/test")
    @app.get("/api/import/")
    @app.get("/api/sources/")
    def business():
        return {"ok": True}
    return app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.network = patch("socket.create_connection", side_effect=AssertionError("Network forbidden"))
        self.network.start()

    def tearDown(self):
        self.client.close()
        self.network.stop()

    def login(self, client=None, password=PASSWORD):
        return (client or self.client).post("/api/auth/login", json={"password": password}, headers={"Origin": ORIGIN})

    def test_all_business_and_documentation_are_protected(self):
        for path in ["/api/notes/", "/api/ai/test", "/api/import/", "/api/sources/", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json", "/api/unknown", "/api/auth/me"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get("/api/health").json(), {"status": "healthy"})
        self.assertEqual(self.client.post("/api/health").status_code, 403)

    def test_login_me_logout_cookie_contract(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        for flag in [COOKIE_NAME, "HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=60"]:
            self.assertIn(flag, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/api/auth/me").json(), {"authenticated": True})
        self.assertEqual(self.client.get("/api/notes/").status_code, 200)
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 403)
        response = self.client.post("/api/auth/logout", headers={"Origin": ORIGIN})
        self.assertEqual(response.json(), {"authenticated": False})
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_strict_origin_for_login_and_all_non_get(self):
        for origin in [None, "null", "https://evil.test", ORIGIN + "/", "http://pkm.test"]:
            headers = {} if origin is None else {"Origin": origin}
            self.assertEqual(self.client.post("/api/auth/login", json={"password": PASSWORD}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/login", json={"password": PASSWORD}, headers=[("Origin", ORIGIN), ("Origin", ORIGIN)]).status_code, 403)
        self.login()
        for method in ["POST", "PATCH", "DELETE", "HEAD", "OPTIONS"]:
            self.assertEqual(self.client.request(method, "/api/notes/").status_code, 403)
        self.assertEqual(self.client.post("/api/notes/", headers={"Origin": ORIGIN}).status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/notes/", headers={"Authorization": "Bearer arbitrary"}).status_code, 403)

    def test_bad_password_and_bounded_payload(self):
        self.assertEqual(self.login(password="wrong").status_code, 401)
        for body in [{}, [], None, {"password": 1}, {"password": PASSWORD, "extra": 1}, {"password": "x" * 1025}]:
            self.assertEqual(self.client.post("/api/auth/login", json=body, headers={"Origin": ORIGIN}).status_code, 422)
        self.assertEqual(self.client.post("/api/auth/login", content=b"x" * 4097, headers={"Origin": ORIGIN}).status_code, 413)

    def test_fail_closed_for_unconfigured_or_invalid_credentials(self):
        for overrides in [{"SECRET_KEY": ""}, {"SECRET_KEY": "your-secret-key-change-this"}, {"PKM_PASSWORD_HASH": ""}, {"PKM_PASSWORD_HASH": "plaintext"}, {"PKM_PUBLIC_ORIGIN": ""}, {"PKM_PUBLIC_ORIGIN": "http://pkm.test"}, {"PKM_PUBLIC_ORIGIN": ORIGIN + "/"}, {"PKM_SESSION_TTL_SECONDS": 0}, {"PKM_TRUSTED_PROXY_PEERS": "*"}, {"PKM_TRUSTED_PROXY_PEERS": "8.8.8.8"}]:
            with self.subTest(overrides=overrides), TestClient(make_app(**overrides), base_url=ORIGIN) as client:
                self.assertEqual(client.get("/api/notes/").status_code, 503)
                self.assertEqual(client.get("/docs").status_code, 503)
                self.assertEqual(self.login(client).status_code, 503)
                self.assertEqual(client.get("/api/health").json(), {"status": "healthy"})

    def test_https_and_explicit_proxy_peer(self):
        for peer, trusted, forwarded, expected in [
            ("127.0.0.1", "", "https", 403),
            ("127.0.0.1", "127.0.0.1", "https", 200),
            ("172.29.91.3", "172.29.91.3", "https", 200),
            ("172.29.91.4", "172.29.91.3", "https", 403),
            ("127.0.0.1", "127.0.0.1", "https,http", 403),
            ("127.0.0.1", "127.0.0.1", "http", 403),
        ]:
            app = make_app(PKM_TRUSTED_PROXY_PEERS=trusted)
            # Keep compatibility with TestClient releases without client= option.
            class PeerApp:
                async def __call__(self, scope, receive, send):
                    if scope["type"] == "http":
                        scope = dict(scope, client=(peer, 12345))
                    await app(scope, receive, send)
            with self.subTest(peer=peer, trusted=trusted, forwarded=forwarded), TestClient(PeerApp(), base_url="http://pkm.test") as client:
                response = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN, "X-Forwarded-Proto": forwarded, "X-Forwarded-For": "127.0.0.1"})
                self.assertEqual(response.status_code, expected)
                if expected == 200:
                    self.assertIn("Secure", response.headers["set-cookie"])

    def test_http_defaults_fail_closed_and_requires_literal_ip(self):
        for overrides in [
            {}, {"PKM_ALLOW_INSECURE_HTTP": False}, {"PKM_ALLOW_INSECURE_HTTP": "true"},
            *({"PKM_ALLOW_INSECURE_HTTP": True, "PKM_PUBLIC_ORIGIN": origin} for origin in [
                "http://pkm.test", "http://localhost", "http://127.1", "http://2130706433",
                "http://127.0.0.1/", "http://127.0.0.1?", "http://127.0.0.1#",
                "http://user@127.0.0.1", "http://127.0.0.1:", "http://127.0.0.1:0",
                "http://127.0.0.1:65536", "http://[fe80::1%25eth0]", "ftp://127.0.0.1",
            ]),
        ]:
            values = {"PKM_PUBLIC_ORIGIN": "http://127.0.0.1:8091", **overrides}
            with self.subTest(overrides=overrides), TestClient(make_app(**values), base_url="http://127.0.0.1:8091") as client:
                self.assertEqual(client.get("/api/auth/me").status_code, 503)
                self.assertEqual(client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": values["PKM_PUBLIC_ORIGIN"]}).status_code, 503)
                self.assertEqual(client.get("/api/health").status_code, 200)

    def test_explicit_http_ip_login_cookie_origin_and_logout(self):
        # This pinned Starlette TestClient cannot parse bracketed IPv6 URLs;
        # verify IPv6 configuration directly and exercise HTTP over IPv4 below.
        ipv6 = AuthState(config(PKM_PUBLIC_ORIGIN="http://[::1]:8091", PKM_ALLOW_INSECURE_HTTP=True))
        self.assertTrue(ipv6.ready)
        self.assertEqual(ipv6.cookie_name, "pkm_session")
        self.assertFalse(ipv6.cookie_secure)
        for origin in ["http://127.0.0.1:8091", "http://192.0.2.10", "http://8.133.220.164:8091"]:
            app = make_app(PKM_PUBLIC_ORIGIN=origin, PKM_ALLOW_INSECURE_HTTP=True)
            with self.subTest(origin=origin), TestClient(app, base_url=origin) as client:
                self.assertEqual(client.get("/api/auth/me").status_code, 401)
                for bad in [None, "null", origin + "/", "http://evil.test", "http://127.0.0.1:8092", origin.replace("http:", "https:")]:
                    headers = {} if bad is None else {"Origin": bad}
                    self.assertEqual(client.post("/api/auth/login", json={"password": PASSWORD}, headers=headers).status_code, 403)
                self.assertEqual(client.post("/api/auth/login", json={"password": PASSWORD}, headers=[("Origin", origin), ("Origin", origin)]).status_code, 403)
                response = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": origin})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"authenticated": True})
                cookie = response.headers["set-cookie"]
                self.assertTrue(cookie.startswith("pkm_session="))
                for flag in ["HttpOnly", "SameSite=strict", "Path=/", "Max-Age=60"]:
                    self.assertIn(flag, cookie)
                for flag in ["__Host-", "Secure", "Domain="]:
                    self.assertNotIn(flag, cookie)
                self.assertEqual(client.get("/api/auth/me").json(), {"authenticated": True})
                for method in ["POST", "PATCH", "DELETE", "HEAD", "OPTIONS"]:
                    self.assertEqual(client.request(method, "/api/notes/", headers={"Origin": "http://evil.test"}).status_code, 403)
                self.assertEqual(client.post("/api/notes/", headers={"Origin": origin}).status_code, 200)
                response = client.post("/api/auth/logout", headers={"Origin": origin})
                self.assertEqual(response.json(), {"authenticated": False})
                self.assertTrue(response.headers["set-cookie"].startswith("pkm_session="))
                self.assertIn("Max-Age=0", response.headers["set-cookie"])
                self.assertNotIn("Secure", response.headers["set-cookie"])
                self.assertEqual(client.get("/api/auth/me").status_code, 401)
                client.cookies.set(COOKIE_NAME, app.state.pkm_auth.issue())
                self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_http_login_rate_limit_is_unchanged(self):
        origin = "http://127.0.0.1:8091"
        with TestClient(make_app(PKM_PUBLIC_ORIGIN=origin, PKM_ALLOW_INSECURE_HTTP=True, PKM_LOGIN_MAX_ATTEMPTS=1), base_url=origin) as client:
            self.assertEqual(client.post("/api/auth/login", json={}, headers={"Origin": origin}).status_code, 422)
            response = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": origin})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "60")

    def test_https_stays_secure_even_with_http_opt_in(self):
        app = make_app(PKM_ALLOW_INSECURE_HTTP=True)
        self.assertFalse(app.state.pkm_auth.allow_insecure_http)
        with TestClient(app, base_url="http://pkm.test") as client:
            self.assertEqual(self.login(client).status_code, 403)
        with TestClient(app, base_url=ORIGIN) as client:
            client.cookies.set("pkm_session", app.state.pkm_auth.issue())
            self.assertEqual(client.get("/api/auth/me").status_code, 401)
            response = self.login(client)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["set-cookie"].startswith(COOKIE_NAME + "="))
            self.assertIn("Secure", response.headers["set-cookie"])
            self.assertEqual(client.get("/api/auth/me").status_code, 200)
            response = client.post("/api/auth/logout", headers={"Origin": ORIGIN})
            self.assertTrue(response.headers["set-cookie"].startswith(COOKIE_NAME + "="))
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_session_expiry_signature_and_rotation(self):
        state = self.app.state.pkm_auth
        token = state.issue(now=1000)
        self.assertTrue(state.valid(token, now=1000))
        self.assertTrue(state.valid(token, now=1059))
        self.assertFalse(state.valid(token, now=1060))
        self.assertFalse(state.valid(token, now=999))
        for bad in ["", "x" * 10000, token.replace("1060", "9999"), token[:-1] + ("a" if token[-1] != "a" else "b")]:
            self.assertFalse(state.valid(bad, now=1001))
        self.assertFalse(AuthState(config(SECRET_KEY="b" * 48)).valid(token, now=1001))
        self.assertFalse(AuthState(config(PKM_PASSWORD_HASH=hash_password("new-password"))).valid(token, now=1001))
        self.client.cookies.set(COOKIE_NAME, state.issue(now=int(time.time()) - 100))
        self.assertEqual(self.client.get("/api/notes/").status_code, 401)

    def test_password_hash_standard_library_and_constant_time(self):
        algorithm, rounds, salt, digest = HASH.split("$")
        self.assertEqual(algorithm, "pbkdf2_sha256")
        self.assertEqual(hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), bytes.fromhex(salt), int(rounds)).hex(), digest)
        with patch("app.core.auth.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as compare:
            self.assertTrue(self.app.state.pkm_auth.verify_password(PASSWORD))
            self.assertTrue(compare.called)

    def test_rate_limit_is_global_bounded_and_expires(self):
        with TestClient(make_app(PKM_LOGIN_MAX_ATTEMPTS=2), base_url=ORIGIN) as client:
            self.assertEqual(self.login(client, "wrong").status_code, 401)
            self.assertEqual(self.login(client, "wrong").status_code, 401)
            response = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN, "X-Forwarded-For": "1.2.3.4"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "60")
        clock = [0]
        limiter = LoginLimiter(2, 60, clock=lambda: clock[0])
        self.assertTrue(limiter.allow())
        self.assertTrue(limiter.allow())
        for _ in range(10000):
            self.assertFalse(limiter.allow())
        self.assertEqual(limiter.count, 2)
        clock[0] = 60
        self.assertTrue(limiter.allow())

    def test_real_app_wiring_in_isolated_subprocess(self):
        script = '''
import socket
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Network forbidden"))
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
settings = Settings(_env_file=None, DATABASE_URL="sqlite://", SECRET_KEY="", PKM_PASSWORD_HASH="", PKM_PUBLIC_ORIGIN="")
assert settings.PKM_ALLOW_INSECURE_HTTP is False
assert settings.PKM_TEST_PUBLIC_SYNC is False
import os
os.environ['PKM_TEST_PUBLIC_SYNC'] = 'true'
assert Settings(_env_file=None).PKM_TEST_PUBLIC_SYNC is True
os.environ['PKM_TEST_PUBLIC_SYNC'] = 'false'
assert Settings(_env_file=None).PKM_TEST_PUBLIC_SYNC is False
os.environ['PKM_ALLOW_INSECURE_HTTP'] = 'true'
assert Settings(_env_file=None).PKM_ALLOW_INSECURE_HTTP is True
os.environ['PKM_ALLOW_INSECURE_HTTP'] = 'false'
assert Settings(_env_file=None).PKM_ALLOW_INSECURE_HTTP is False
app = create_app(settings, initialize_database=False)
assert any(getattr(r, "path", None) == "/api/sources/" for r in app.routes)
with TestClient(app, base_url="https://pkm.test") as client:
    assert client.get("/api/health").json() == {"status": "healthy"}
    for path in ["/api/notes/", "/api/import/", "/api/sources/", "/api/ai/test", "/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 503, path
'''
        env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), DATABASE_URL="sqlite://", DEBUG="false")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-c", script], cwd=directory, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
