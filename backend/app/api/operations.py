"""Admin operations; parent app MUST install global PKM authentication.

Mount with prefix=/api. No arbitrary archive/path uploads and no live restore.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import backup_service as service
from app.core import operations_service as operations
from app.core.database import get_db

router = APIRouter(prefix="/operations", tags=["operations"])


def _error(status, code, message):
    return HTTPException(status, detail={"code": code, "message": message},
                         headers={"Cache-Control": "no-store"})


@router.get("/status")
def status(db: Session = Depends(get_db)):
    try:
        return operations.status_snapshot(db)
    except Exception:
        raise _error(503, "operations_unavailable", "Operations status is unavailable") from None


@router.post("/backups/{backup_id}/rehearse")
async def rehearse(backup_id: str):
    if not operations.BACKUP_ID.fullmatch(backup_id):
        raise _error(404, "backup_not_found", "Backup not found")
    try:
        item = await asyncio.to_thread(service.lookup, backup_id)
    except Exception:
        raise _error(503, "backup_unavailable", "Backup inventory is unavailable") from None
    if not item:
        raise _error(404, "backup_not_found", "Backup not found")
    try:
        return await asyncio.to_thread(operations.rehearse_backup, item)
    except Exception:
        raise _error(422, "rehearsal_failed", "Backup rehearsal failed; do not use this archive for recovery") from None
