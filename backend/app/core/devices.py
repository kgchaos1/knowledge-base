"""Device pairing and narrow read-only bearer authentication.

Parent integration: mount api.devices.router at /api; import before create_all.
For the *exact* POST /api/devices/claim, call claim_transport_guard(request,
app.state.pkm_auth) before body parsing and skip only the session requirement.
The guard is idempotent per Request so the endpoint can also fail closed.
For bearer requests: is_device_route(method, path), then authenticate_device.
Do not fall back to an admin session when an Authorization header is present.
Global claim throttling is constant-space, per process: use one worker or add
shared edge throttling. Device tokens do not expire; revoke in DB to invalidate.
"""
import base64
from datetime import datetime, timezone
import hashlib
import re
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from app.core.auth import LoginLimiter
from app.core.database import SessionLocal
from app.models_device import Device, Pairing

PAIRING_TTL_SECONDS = 600
MAX_DEVICES = 1000  # Includes revoked rows; no unbounded device-history growth.
CLAIM_MAX_ATTEMPTS = 20
CLAIM_WINDOW_SECONDS = 60
claim_limiter = LoginLimiter(CLAIM_MAX_ATTEMPTS, CLAIM_WINDOW_SECONDS)
_CODE = re.compile(r"[A-Z2-7]{20}\Z")  # 100 bits, not an eight-digit PIN.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")  # token_urlsafe(32): 256 bits.
_ATTACHMENT = re.compile(r"/api/attachments/[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)\Z")


def timestamp(value):
    return None if value is None else datetime.fromtimestamp(value, timezone.utc).isoformat()


def public_device(device):
    return {"id": device.id, "name": device.name,
            "created_at": timestamp(device.created_at),
            "last_seen": timestamp(device.last_seen), "revoked": bool(device.revoked)}


def validate_name(name):
    try:
        valid = (isinstance(name, str) and bool(name.strip()) and len(name) <= 120
                 and len(name.encode("utf-8")) <= 360
                 and not any(ord(c) < 32 or ord(c) == 127 for c in name))
    except UnicodeError:
        valid = False
    if not valid:
        raise HTTPException(422, "Name must be 1-120 printable characters (maximum 360 UTF-8 bytes)")
    return name.strip()


def _digest(secret):
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def is_device_route(method, path):
    """Exact methods/paths only; no prefixes, trailing slash or generic sync GET."""
    return ((method, path) in {("GET", "/api/sync/manifest"), ("GET", "/api/sync/sources"), ("POST", "/api/sync/batch")}
            or (method == "GET" and _ATTACHMENT.fullmatch(path) is not None))


def claim_transport_guard(request, state=None):
    """Validate parent's transport/Origin and consume budget before body parsing."""
    if getattr(request.state, "pkm_claim_checked", False):
        return
    state = state if state is not None else getattr(request.app.state, "pkm_auth", None)
    if state is None or not state.ready:
        raise HTTPException(503, "Authentication is not configured")
    if not state.secure_transport(request):
        raise HTTPException(403, "HTTPS is required")
    if request.headers.getlist("origin") != [state.origin]:
        raise HTTPException(403, "Origin is not allowed")
    if not claim_limiter.allow():
        raise HTTPException(429, "Too many pairing attempts", headers={"Retry-After": str(CLAIM_WINDOW_SECONDS)})
    request.state.pkm_claim_checked = True


def create_pairing(db, name, *, now=None):
    """Rotate the sole pending code atomically. Previous pending codes die at once."""
    name = validate_name(name)
    now = time.time() if now is None else now
    code = base64.b32encode(secrets.token_bytes(13)).decode("ascii")[:20]
    with db.begin():
        # DELETE is the first SQL statement: acquire SQLite's write lock before
        # reading capacity, serializing generation/claim across connections.
        db.execute(delete(Pairing))
        if db.scalar(select(func.count()).select_from(Device)) >= MAX_DEVICES:
            raise HTTPException(409, "Device capacity reached")
        db.add(Pairing(code_hash=_digest(code), name=name, created_at=now,
                       expires_at=now + PAIRING_TTL_SECONDS))
    return {"code": code, "expires_at": timestamp(now + PAIRING_TTL_SECONDS)}


def claim_pairing(db, code, name, *, now=None):
    name = validate_name(name)
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        raise HTTPException(401, "Invalid or expired pairing code")
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    device_id = secrets.token_hex(16)
    with db.begin():
        # Conditional DELETE claims the secret exactly once, even across workers.
        # Device insertion and consumption commit together; any failure restores
        # the pairing. No read-then-write race and no Python process-only lock.
        consumed = db.execute(delete(Pairing).where(Pairing.code_hash == _digest(code), Pairing.expires_at > now))
        if consumed.rowcount != 1:
            raise HTTPException(401, "Invalid or expired pairing code")
        if db.scalar(select(func.count()).select_from(Device)) >= MAX_DEVICES:
            raise HTTPException(409, "Device capacity reached")
        db.add(Device(id=device_id, name=name, token_hash=_digest(token),
                      created_at=now, last_seen=None, revoked=False))
    return {"device_id": device_id, "token": token, "expires_at": None}


def authenticate_device(token, db=None):
    """Return detached Device or None; hashes only, no auth cache, checks every use.

    Optional db must be a clean Session without an open transaction. A default
    SessionLocal is owned/closed here; parent tests may pass an isolated Session.
    """
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        return None
    owned = db is None
    db = SessionLocal() if owned else db
    try:
        now = time.time()
        digest = _digest(token)
        with db.begin():
            device = db.scalar(select(Device).where(Device.token_hash == digest, Device.revoked.is_(False)))
            if device is None:
                return None
            db.expunge(device)
        # Most sync requests are read-only; never upgrade a SQLite read txn to a
        # write txn. Recheck revocation in the conditional last-seen update.
        if device.last_seen is None or device.last_seen < now - 60:
            with db.begin():
                changed = db.execute(update(Device).where(Device.id == device.id, Device.revoked.is_(False)).values(last_seen=now))
                if changed.rowcount != 1:
                    return None
            device.last_seen = now
        return device
    finally:
        if owned:
            db.close()


def list_devices(db):
    with db.begin():
        return [public_device(device) for device in db.scalars(select(Device).order_by(Device.created_at, Device.id))]


def revoke_device(db, device_id):
    with db.begin():
        result = db.execute(update(Device).where(Device.id == device_id).values(revoked=True))
        if result.rowcount != 1:
            raise HTTPException(404, "Device not found")
    return {"id": device_id, "revoked": True}
