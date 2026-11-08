"""Admin-only device management; claim is public only after strict transport guard.

Mount at /api. No endpoint ever lists a token/hash or a pending pairing code.
Request bodies are streamed under a hard 4096-byte cap before JSON decoding.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.devices import (claim_pairing, claim_transport_guard, create_pairing,
                              list_devices, revoke_device)

router = APIRouter(prefix="/devices", tags=["devices"])


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"


def require_admin(request: Request):
    state = getattr(request.app.state, "pkm_auth", None)
    if state is None or not state.ready:
        raise HTTPException(503, "Authentication is not configured")
    if not state.secure_transport(request):
        raise HTTPException(403, "HTTPS is required")
    if request.method != "GET" and request.headers.getlist("origin") != [state.origin]:
        raise HTTPException(403, "Origin is not allowed")
    if request.headers.getlist("authorization") or not state.valid(request.cookies.get(state.cookie_name)):
        raise HTTPException(401, "Administrator session required")


async def _body(request, fields):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise HTTPException(413, "Device request body is too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != fields or any(not isinstance(v, str) for v in payload.values()):
            raise ValueError()
        return payload
    except (ValueError, TypeError, UnicodeError):
        # Do not echo validation inputs: claim bodies contain credentials.
        raise HTTPException(422, "Provide exactly the required string fields") from None


@router.get("/", dependencies=[Depends(require_admin)])
@router.get("", dependencies=[Depends(require_admin)], include_in_schema=False)
def index(response: Response, db: Session = Depends(get_db)):
    _no_store(response)
    return list_devices(db)


@router.post("/pairing", dependencies=[Depends(require_admin)])
async def pairing(request: Request, response: Response, db: Session = Depends(get_db)):
    _no_store(response)
    body = await _body(request, {"name"})
    return create_pairing(db, body["name"])


@router.post("/claim")
async def claim(request: Request, response: Response, db: Session = Depends(get_db)):
    _no_store(response)
    claim_transport_guard(request)
    body = await _body(request, {"code", "name"})
    return claim_pairing(db, body["code"], body["name"])


@router.post("/{device_id}/revoke", dependencies=[Depends(require_admin)])
def revoke(device_id: str, response: Response, db: Session = Depends(get_db)):
    _no_store(response)
    return revoke_device(db, device_id)
