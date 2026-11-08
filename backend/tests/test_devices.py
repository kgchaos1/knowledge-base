"""Offline device tests; no production DB, server, network or .env access.

Run from repository: .venv-test/Scripts/python -m unittest discover -s
backend/tests -p test_devices.py -v
"""
import concurrent.futures
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.devices import router
from app.core.auth import AuthState, LoginLimiter, install_auth
from app.core.database import Base, get_db
from app.core import devices
from app.models_device import Device, Pairing
from test_auth import config, ORIGIN


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("socket.create_connection", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.temp.name) / "devices.db"), connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine, tables=[Device.__table__, Pairing.__table__])
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.limiter = patch.object(devices, "claim_limiter", LoginLimiter(20, 60))
        self.limiter.start()
        self.app = FastAPI()
        self.app.state.pkm_auth = AuthState(config())
        self.app.include_router(router, prefix="/api")
        def get_test_db():
            with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(self.app, base_url=ORIGIN)

    def tearDown(self):
        self.client.close()
        self.limiter.stop()
        self.engine.dispose()
        self.temp.cleanup()
        self.network.stop()

    def admin(self):
        state = self.app.state.pkm_auth
        self.client.cookies.set(state.cookie_name, state.issue())
        return {"Origin": ORIGIN}

    def pair(self, name="Tablet"):
        response = self.client.post("/api/devices/pairing", json={"name": name}, headers=self.admin())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def claim(self, code, name="Offline reader"):
        self.client.cookies.clear()
        return self.client.post("/api/devices/claim", json={"code": code, "name": name}, headers={"Origin": ORIGIN})

    def test_pair_claim_list_revoke_hash_only(self):
        pair = self.pair()
        self.assertRegex(pair["code"], r"^[A-Z2-7]{20}$")
        with self.sessions() as db:
            pending = db.scalar(select(Pairing))
            self.assertEqual(pending.code_hash, hashlib.sha256(pair["code"].encode()).hexdigest())
            self.assertEqual(pending.expires_at - pending.created_at, 600)
            self.assertNotIn(pair["code"], repr(pending.__dict__))
        response = self.claim(pair["code"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertRegex(result["device_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(result["token"], r"^[A-Za-z0-9_-]{43}$")
        self.assertIsNone(result["expires_at"])
        with self.sessions() as db:
            row = db.get(Device, result["device_id"])
            self.assertEqual(row.token_hash, hashlib.sha256(result["token"].encode()).hexdigest())
            self.assertNotIn(result["token"], repr(row.__dict__))
            self.assertIsNone(db.scalar(select(Pairing)))
        with self.sessions() as db:
            authenticated = devices.authenticate_device(result["token"], db)
            self.assertEqual(authenticated.id, result["device_id"])
            self.assertIsNotNone(authenticated.last_seen)
        self.admin()
        listed = self.client.get("/api/devices/").json()
        self.assertEqual(set(listed[0]), {"id", "name", "created_at", "last_seen", "revoked"})
        self.assertNotIn(result["token"], str(listed))
        self.assertNotIn("token_hash", str(listed))
        self.assertEqual(self.claim(pair["code"]).status_code, 401)
        response = self.client.post(f'/api/devices/{result["device_id"]}/revoke', headers=self.admin())
        self.assertEqual(response.status_code, 200)
        with self.sessions() as db:
            self.assertIsNone(devices.authenticate_device(result["token"], db))
        self.assertTrue(self.client.get("/api/devices/").json()[0]["revoked"])

    def test_pairing_rotation_expiry_and_invalid_codes(self):
        with self.sessions() as db:
            first = devices.create_pairing(db, "first", now=1000)
            second = devices.create_pairing(db, "second", now=1001)
            for code, now in [(first["code"], 1002), (second["code"], 1601)]:
                with self.assertRaises(HTTPException) as caught:
                    devices.claim_pairing(db, code, "reader", now=now)
                self.assertEqual(caught.exception.status_code, 401)
            self.assertIsNone(devices.authenticate_device("x" * 43, db))
        for bad in ["", "12345678", "A" * 10000, "a" * 20, "é" * 20]:
            self.assertIn(self.claim(bad).status_code, [401, 413])
        for bad in [None, {}, 1, []]:
            response = self.client.post("/api/devices/claim", json={"code": bad, "name": "reader"}, headers={"Origin": ORIGIN})
            self.assertEqual(response.status_code, 422)

    def test_concurrent_claim_exactly_one_winner(self):
        pair = self.pair()
        def attempt(_):
            with self.sessions() as db:
                try:
                    return devices.claim_pairing(db, pair["code"], "reader")
                except HTTPException as exc:
                    return exc.status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(results.count(401), 7)
        with self.sessions() as db:
            self.assertEqual(len(list(db.scalars(select(Device)))), 1)

    def test_failed_device_insert_rolls_back_code_consumption(self):
        pair = self.pair()
        with self.sessions() as db:
            with patch.object(devices, "MAX_DEVICES", 0), self.assertRaises(HTTPException):
                devices.claim_pairing(db, pair["code"], "reader")
            result = devices.claim_pairing(db, pair["code"], "reader")
            self.assertIn("token", result)

    def test_admin_session_required_and_bearer_never_admin(self):
        for path, method in [("/api/devices/", "GET"), ("/api/devices/pairing", "POST"), ("/api/devices/missing/revoke", "POST")]:
            response = self.client.request(method, path, headers={"Origin": ORIGIN}, json={"name": "reader"})
            self.assertEqual(response.status_code, 401)
            self.admin()
            response = self.client.request(method, path, headers={"Origin": ORIGIN, "Authorization": "Bearer arbitrary"}, json={"name": "reader"})
            self.assertEqual(response.status_code, 401)
            self.client.cookies.clear()
        self.admin()
        self.assertEqual(self.client.post("/api/devices/missing/revoke", headers={"Origin": ORIGIN}).status_code, 404)
        self.assertEqual(self.client.post("/api/devices/pairing", json={"name": "reader"}).status_code, 403)

    def test_claim_strict_origin_transport_and_configuration(self):
        for origin in [None, "null", ORIGIN + "/", "https://evil.test"]:
            headers = {} if origin is None else {"Origin": origin}
            self.assertEqual(self.client.post("/api/devices/claim", json={}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/devices/claim", json={}, headers=[("Origin", ORIGIN), ("Origin", ORIGIN)]).status_code, 403)
        with TestClient(self.app, base_url="http://pkm.test") as client:
            self.assertEqual(client.post("/api/devices/claim", json={}, headers={"Origin": ORIGIN}).status_code, 403)
        self.app.state.pkm_auth = AuthState(config(SECRET_KEY=""))
        self.assertEqual(self.claim("A" * 20).status_code, 503)
        origin = "http://127.0.0.1:8091"
        self.app.state.pkm_auth = AuthState(config(PKM_PUBLIC_ORIGIN=origin, PKM_ALLOW_INSECURE_HTTP=True))
        with TestClient(self.app, base_url=origin) as client:
            self.assertEqual(client.post("/api/devices/claim", json={}, headers={"Origin": origin}).status_code, 422)

    def test_body_limits_no_secret_echo_and_global_budget(self):
        secret = "A" * 20
        response = self.client.post("/api/devices/claim", content=b"x" * 4097, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/api/devices/claim", json={"code": secret, "name": 1}, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        for _ in range(18):
            self.assertEqual(self.claim(secret).status_code, 401)
        self.assertEqual(self.claim(secret).status_code, 429)
        self.assertEqual(devices.claim_limiter.count, 20)
        self.assertEqual(self.claim(secret).headers["retry-after"], "60")

    def test_name_validation_and_total_device_limit(self):
        for bad in ["", " ", "x" * 121, "x\n", "x\x7f", "\ud800", 123]:
            response = self.client.post("/api/devices/pairing", json={"name": bad}, headers=self.admin())
            self.assertEqual(response.status_code, 422)
        pair = self.pair()
        with self.sessions() as db:
            devices.claim_pairing(db, pair["code"], "reader")
            with patch.object(devices, "MAX_DEVICES", 1), self.assertRaises(HTTPException):
                devices.create_pairing(db, "extra")

    def test_parent_auth_integration_public_claim_device_scopes_and_revocation(self):
        app = FastAPI()
        install_auth(app, config())
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_db] = self.app.dependency_overrides[get_db]
        @app.get("/api/sync/manifest")
        @app.post("/api/sync/batch")
        @app.get("/api/attachments/{filename}")
        @app.get("/api/notes/")
        @app.get("/api/ai/test")
        @app.get("/api/import/")
        @app.get("/api/backups/")
        def business():
            return {"ok": True}
        with patch.object(devices, "SessionLocal", self.sessions), TestClient(app, base_url=ORIGIN) as client:
            state = app.state.pkm_auth
            client.cookies.set(state.cookie_name, state.issue())
            pair = client.post("/api/devices/pairing", json={"name": "reader"}, headers={"Origin": ORIGIN}).json()
            client.cookies.clear()
            result = client.post("/api/devices/claim", json={"code": pair["code"], "name": "reader"}, headers={"Origin": ORIGIN})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(devices.claim_limiter.count, 1)  # middleware + endpoint, exactly one charge
            token = result.json()["token"]
            device_id = result.json()["device_id"]
            headers = {"Authorization": "Bearer " + token, "Origin": ORIGIN}
            for path, method in [("/api/sync/manifest", "GET"), ("/api/sync/batch", "POST"), ("/api/attachments/" + "a" * 64 + ".png", "GET")]:
                self.assertEqual(client.request(method, path, headers=headers).status_code, 200)
            for path in ["/api/devices/", "/api/notes/", "/api/ai/test", "/api/import/", "/api/backups/", "/openapi.json", "/api/attachments/evil.png"]:
                self.assertEqual(client.get(path, headers=headers).status_code, 403)
            self.assertEqual(client.post("/api/sync/batch", headers={"Authorization": "Bearer " + token}).status_code, 403)
            client.cookies.set(state.cookie_name, state.issue())
            self.assertEqual(client.post(f"/api/devices/{device_id}/revoke", headers={"Origin": ORIGIN}).status_code, 200)
            client.cookies.clear()
            self.assertEqual(client.get("/api/sync/manifest", headers=headers).status_code, 401)

    def test_device_scope_exact_allowlist(self):
        valid = [("GET", "/api/sync/manifest"), ("POST", "/api/sync/batch"), ("GET", "/api/attachments/" + "a" * 64 + ".png")]
        for method, path in valid:
            self.assertTrue(devices.is_device_route(method, path))
        for path in ["/api/sync", "/api/sync/manifest/", "/api/sync/attachments/" + "a" * 64 + ".png", "/api/attachments/../secret", "/api/attachments/" + "A" * 64 + ".png", "/api/attachments/" + "a" * 64 + ".png/extra", "/api/notes/", "/api/ai/test", "/api/import/", "/api/backups/", "/api/devices/", "/api/auth/me", "/openapi.json"]:
            for method in ["GET", "POST", "HEAD", "DELETE"]:
                self.assertFalse(devices.is_device_route(method, path), (method, path))
        self.assertFalse(devices.is_device_route("GET", "/api/sync/batch"))
        self.assertFalse(devices.is_device_route("POST", "/api/sync/manifest"))
        for bad in [None, "", "💣" * 43, "a" * 100000]:
            self.assertIsNone(devices.authenticate_device(bad))


if __name__ == "__main__":
    unittest.main()
