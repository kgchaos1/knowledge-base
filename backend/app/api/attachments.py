"""Authenticated content-addressed image delivery; never accept filesystem paths."""
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix='/attachments', tags=['attachments'])
ROOT = Path('/app/data/attachments')

@router.get('/{filename}')
async def read_attachment(filename: str):
    if not re.fullmatch(r'[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)', filename):
        raise HTTPException(404, 'Attachment not found')
    path = ROOT / filename
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, 'Attachment not found')
    types = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','gif':'image/gif','webp':'image/webp'}
    return FileResponse(path, media_type=types[path.suffix[1:]], headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})
