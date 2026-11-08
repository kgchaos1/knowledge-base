"""Single-user, same-origin PKM authentication (standard-library cryptography).

Deployment: HTTPS is the default. Explicit PKM_ALLOW_INSECURE_HTTP=True permits
only a literal-IP HTTP PKM_PUBLIC_ORIGIN, exposing passwords/sessions in transit.
A local TLS proxy may forward X-Forwarded-Proto only when its literal local/private peer is
explicitly configured (including private container IPs); disable uvicorn
proxy_headers to preserve that peer.
Login throttling is intentionally global, constant-space and per process. Use
one worker or add shared edge throttling for multi-worker deployments.
Logout removes the browser cookie; copied signed cookies expire at their fixed
TTL (or immediately after SECRET_KEY / password hash rotation).
"""
import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

COOKIE_NAME = "__Host-pkm_session"
PASSWORD_ITERATIONS = 600_000


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    """Generate the value of PKM_PASSWORD_HASH offline; never store plaintext."""
    if not password or len(password.encode("utf-8")) > 1024:
        raise ValueError("Password must be between 1 and 1024 UTF-8 bytes")
    if not 600_000 <= iterations <= 2_000_000:
        raise ValueError("PBKDF2 iterations must be between 600000 and 2000000")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def _parse_hash(value):
    match = re.fullmatch(r"pbkdf2_sha256\$([0-9]{6,7})\$([0-9a-f]{32,128})\$([0-9a-f]{64})", value)
    if not match or not 600_000 <= int(match[1]) <= 2_000_000 or len(match[2]) % 2:
        raise ValueError("Invalid password hash")
    return int(match[1]), bytes.fromhex(match[2]), bytes.fromhex(match[3])


class LoginLimiter:
    """Fixed-window global budget; no attacker-controlled keys or growing map."""
    def __init__(self, attempts, window, clock=time.monotonic):
        self.attempts, self.window, self.clock = attempts, window, clock
        self.started, self.count = clock(), 0
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            now = self.clock()
            if now - self.started >= self.window:
                self.started, self.count = now, 0
            if self.count >= self.attempts:
                return False
            self.count += 1
            return True


class AuthState:
    def __init__(self, config):
        self.ready = False
        self.origin = config.PKM_PUBLIC_ORIGIN
        self.ttl = config.PKM_SESSION_TTL_SECONDS
        self.peers = set()
        self.allow_insecure_http = False
        self.cookie_name = COOKIE_NAME
        self.cookie_secure = True
        self.limiter = LoginLimiter(config.PKM_LOGIN_MAX_ATTEMPTS, config.PKM_LOGIN_WINDOW_SECONDS)
        try:
            origin = urlsplit(self.origin)
            if (origin.scheme not in ("https", "http") or not origin.hostname or origin.username
                    or origin.password or origin.path or origin.query or origin.fragment
                    or self.origin != f"{origin.scheme}://{origin.netloc}" or origin.netloc.endswith(":")):
                raise ValueError("An exact origin is required")
            if origin.scheme == "http":
                if getattr(config, "PKM_ALLOW_INSECURE_HTTP", False) is not True:
                    raise ValueError("HTTP requires explicit opt-in")
                # No DNS names, abbreviated IPs, or scoped IPv6 addresses.
                if "%" in origin.hostname:
                    raise ValueError("A literal IP origin is required")
                ipaddress.ip_address(origin.hostname)
                self.allow_insecure_http = True
                self.cookie_name = "pkm_session"
                self.cookie_secure = False
            if origin.port is not None and not 1 <= origin.port <= 65535:
                raise ValueError("Invalid origin port")
            if any(c.isspace() for c in self.origin):
                raise ValueError("Invalid origin")
            if len(config.SECRET_KEY.encode("utf-8")) < 32 or config.SECRET_KEY == "your-secret-key-change-this":
                raise ValueError("Strong secret required")
            if not 60 <= self.ttl <= 604800:
                raise ValueError("TTL must be between one minute and seven days")
            if not 1 <= config.PKM_LOGIN_MAX_ATTEMPTS <= 100 or not 1 <= config.PKM_LOGIN_WINDOW_SECONDS <= 3600:
                raise ValueError("Invalid login limits")
            for peer in filter(None, (p.strip() for p in config.PKM_TRUSTED_PROXY_PEERS.split(","))):
                address = ipaddress.ip_address(peer)
                if not (address.is_loopback or address.is_private) or address.is_unspecified or address.is_multicast:
                    raise ValueError("Only explicitly trusted local/private proxy peers are supported")
                self.peers.add(str(address))
            self.iterations, self.salt, self.digest = _parse_hash(config.PKM_PASSWORD_HASH)
            # Rotation of either server credential invalidates all existing sessions.
            self.key = hmac.digest(config.SECRET_KEY.encode(), b"pkm-session-v1\0" + config.PKM_PASSWORD_HASH.encode(), "sha256")
            self.ready = True
        except (ValueError, TypeError, UnicodeError):
            pass  # Fail closed; do not expose credential or configuration details.

    def secure_transport(self, request):
        if self.allow_insecure_http and request.scope.get("scheme") == "http":
            return True
        if request.scope.get("scheme") == "https":
            return True
        peer = request.client.host if request.client else ""
        try:
            peer = str(ipaddress.ip_address(peer))
        except ValueError:
            return False
        return peer in self.peers and request.headers.getlist("x-forwarded-proto") == ["https"]

    def issue(self, now=None):
        now = int(time.time() if now is None else now)
        payload = f"v1.{now}.{now + self.ttl}.{secrets.token_hex(16)}"
        return f"{payload}.{hmac.new(self.key, payload.encode(), 'sha256').hexdigest()}"

    def valid(self, token, now=None):
        if not token or len(token) > 256:
            return False
        match = re.fullmatch(r"(v1\.([0-9]{1,12})\.([0-9]{1,12})\.[0-9a-f]{32})\.([0-9a-f]{64})", token)
        if not match:
            return False
        signature = hmac.new(self.key, match[1].encode(), "sha256").hexdigest()
        if not hmac.compare_digest(signature, match[4]):
            return False
        issued, expires = int(match[2]), int(match[3])
        now = time.time() if now is None else now
        return issued <= now < expires and expires - issued == self.ttl

    def verify_password(self, password):
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), self.salt, self.iterations)
        return hmac.compare_digest(candidate, self.digest)


def _error(status, message, **headers):
    return JSONResponse({"detail": message}, status_code=status, headers={"Cache-Control": "no-store", **headers})


def install_auth(app, config):
    """Install on an isolated FastAPI app without importing global settings/DB."""
    state = AuthState(config)
    app.state.pkm_auth = state

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        protected = path == "/api" or path.startswith("/api/") or path in ("/docs", "/redoc", "/openapi.json") or path.startswith(("/docs/", "/redoc/"))
        if not protected or (path == "/api/health" and request.method == "GET"):
            return await call_next(request)
        if not state.ready:
            return _error(503, "Authentication is not configured")
        if not state.secure_transport(request):
            return _error(403, "HTTPS is required")
        # Strictly all non-GET requests, including login and logout. No bearer
        # token or missing-Origin exception; CLI must explicitly send Origin.
        if request.method != "GET" and request.headers.getlist("origin") != [state.origin]:
            return _error(403, "Origin is not allowed")
        # Explicit, reversible test-only exception; never bypass transport,
        # Origin or authentication on administrative/write endpoints. Supplied
        # credentials still follow normal validation (no invalid-token fallback).
        if getattr(config, 'PKM_TEST_PUBLIC_SYNC', False) is True and not request.headers.getlist('authorization'):
            from app.core.devices import is_device_route
            if is_device_route(request.method, path):
                response = await call_next(request)
                response.headers['Cache-Control'] = 'no-store'
                response.headers['X-PKM-Test-Public-Sync'] = 'true'
                return response
        if path == '/api/devices/claim' and request.method == 'POST':
            from app.core.devices import claim_transport_guard
            try:
                claim_transport_guard(request, state)
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc, HTTPException):
                    return _error(exc.status_code, str(exc.detail), **(exc.headers or {}))
                return _error(503, 'Device pairing unavailable')
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
        authorization = request.headers.get('authorization', '')
        if authorization:
            from app.core.devices import authenticate_device, is_device_route
            if (len(request.headers.getlist('authorization')) != 1 or not authorization.startswith('Bearer ')
                    or not is_device_route(request.method, path)):
                return _error(403, 'Device credentials are read-only')
            device = await run_in_threadpool(authenticate_device, authorization[7:])
            if not device:
                return _error(401, 'Device credential invalid or revoked')
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
        if path == "/api/auth/login" and request.method == "POST":
            # Throttle before body parsing / PBKDF2, even malformed requests.
            if not state.limiter.allow():
                return _error(429, "Too many login attempts", **{"Retry-After": str(state.limiter.window)})
        elif not state.valid(request.cookies.get(state.cookie_name)):
            return _error(401, "Authentication required")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login")
    async def login(request: Request):
        # Stream with a hard bound; do not buffer arbitrarily large login bodies.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 4096:
                return _error(413, "Login body is too large")
            body.extend(chunk)
        try:
            import json
            payload = json.loads(body)
            password = payload.get("password") if isinstance(payload, dict) else None
            if (set(payload) != {"password"} or not isinstance(password, str)
                    or not 1 <= len(password.encode("utf-8")) <= 1024):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            return _error(422, "Provide a password string (maximum 1024 UTF-8 bytes)")
        if not await run_in_threadpool(state.verify_password, password):
            return _error(401, "Invalid credentials")
        response = JSONResponse({"authenticated": True})
        response.set_cookie(state.cookie_name, state.issue(), max_age=state.ttl, secure=state.cookie_secure, httponly=True, samesite="strict", path="/")
        return response

    @router.post("/logout")
    async def logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(state.cookie_name, path="/", secure=state.cookie_secure, httponly=True, samesite="strict")
        return response

    @router.get("/me")
    async def me():
        return {"authenticated": True}

    app.include_router(router)
