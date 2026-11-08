import asyncio
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from app.core import backup_service as service
from app.core.backup_archive import verify_archive

router = APIRouter(prefix='/backups', tags=['backups'])

class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    hour: int = Field(ge=0, le=23)
    keep: int = Field(ge=3, le=30)

@router.get('/')
def list_archives():
    return service.list_backups()

@router.get('/settings')
def settings_read():
    return service.schedule_settings()

@router.patch('/settings')
def settings_write(payload: ScheduleUpdate):
    if service.STATE['running']:
        raise HTTPException(409, '备份进行中，请稍后修改')
    return service.save_settings(payload.model_dump())

@router.get('/status')
def status():
    return service.STATE.copy()

@router.post('/', status_code=202)
async def create(background: BackgroundTasks):
    if service.STATE['running']:
        raise HTTPException(409, '已有备份正在进行')
    service.STATE['running'] = True
    background.add_task(service.make_backup)
    return {'status': 'pending'}

@router.get('/{backup_id}/download')
async def download(backup_id: str):
    item = service.lookup(backup_id)
    if not item:
        raise HTTPException(404, '备份不存在')
    path = service.ROOT / item['filename']
    if path.parent.resolve() != service.ROOT.resolve() or path.is_symlink():
        raise HTTPException(404, '备份不存在')
    return FileResponse(path, filename=item['filename'], media_type='application/zip', headers={'Cache-Control':'no-store'})

@router.post('/{backup_id}/verify')
async def verify(backup_id: str):
    item = service.lookup(backup_id)
    if not item:
        raise HTTPException(404, '备份不存在')
    try:
        result = await asyncio.to_thread(verify_archive, service.ROOT / item['filename'])
        return {'verified': True, 'notes_count': result.get('notes_count',0), 'files_count': result.get('files_count',len(result.get('files',[])))}
    except Exception:
        raise HTTPException(422, '备份校验未通过，请勿用于恢复') from None
