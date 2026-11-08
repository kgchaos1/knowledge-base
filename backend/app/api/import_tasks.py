import asyncio
import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.config import settings
from app.models.note import DataSource, ImportTask, Note
from app.models.schemas import ImportTaskCreate, ImportTaskResponse, normalize_collection_url

router = APIRouter(prefix="/import", tags=["import"])
logger = logging.getLogger(__name__)
ACTIVE_STATUSES = ("pending", "running")


def validate_import_config(platform: str, config: dict) -> dict:
    """Whitelist non-secret settings before persistence AND execution.

    Unknown keys are rejected rather than recursively trying to identify every
    possible secret spelling. Importers obtain credentials only from settings.
    """
    fields = {
        "zhihu": {"collection_url"},
        "yuque": {"namespace"},
        "wechat": {"file_path", "file_type"},
        "douyin": {"file_path"},
    }
    if platform not in fields:
        raise HTTPException(400, "Unsupported or unimplemented import platform")
    if not isinstance(config, dict) or set(config) - fields[platform]:
        raise HTTPException(422, "Unsupported config fields; credentials must be configured on the server")
    if any(not isinstance(value, str) or not value for value in config.values()):
        raise HTTPException(422, "Import settings must be non-empty strings")
    result = dict(config)
    required = {"zhihu": "collection_url", "yuque": "namespace", "wechat": "file_path", "douyin": "file_path"}[platform]
    if required not in result:
        if platform == "douyin":
            raise HTTPException(400, "Douyin online import is not implemented; file_path is required")
        raise HTTPException(422, f"{required} is required")
    if platform == "zhihu":
        try:
            result["collection_url"] = normalize_collection_url(result["collection_url"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    if platform == "wechat" and result.get("file_type", "html") not in ("html", "text"):
        raise HTTPException(422, "file_type must be html or text")
    return result


@router.get("/", response_model=List[ImportTaskResponse])
async def list_import_tasks(db: Session = Depends(get_db)):
    return db.query(ImportTask).order_by(ImportTask.created_at.desc()).all()


@router.get("/{task_id}", response_model=ImportTaskResponse)
async def get_import_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(ImportTask, task_id)
    if task is None:
        raise HTTPException(404, "Import task not found")
    return task


@router.post("/", response_model=ImportTaskResponse)
async def create_import_task(task: ImportTaskCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    config = validate_import_config(task.platform, task.config)
    db_task = ImportTask(platform=task.platform, config=config, status="pending")
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    background_tasks.add_task(run_import_task, db_task.id)
    return db_task


def run_import_task(task_id: int):
    """Starlette runs this sync callable in its thread pool, not the event loop.

    Pass only a durable ID; never retain a request-scoped Session/ORM object.
    """
    asyncio.run(_execute_import_task(task_id))


async def _execute_import_task(task_id: int):
    with SessionLocal() as db:
        # Atomic claim also prevents duplicate delivery of the background job.
        claimed = db.query(ImportTask).filter(
            ImportTask.id == task_id, ImportTask.status == "pending"
        ).update({"status": "running"}, synchronize_session=False)
        db.commit()
        if not claimed:
            return
        task = db.get(ImportTask, task_id)
        try:
            config = validate_import_config(task.platform, task.config)
            if task.platform == "zhihu":
                if not settings.ZHIHU_COOKIE:
                    raise HTTPException(422, "ZHIHU_COOKIE is not configured on the server")
                from app.importers.zhihu import import_from_zhihu
                source = db.query(DataSource).filter(DataSource.active_task_id == task_id).first()
                importer_config = dict(config, _enrich=True)
                if source:
                    importer_config['_source_id'] = source.id
                result = await import_from_zhihu(importer_config, db)
                ai_done = ai_failed = 0
                if source and source.auto_ai:
                    from app.ai.service import ai_service
                    for note_id in result.get('changed_note_ids', []):
                        note = db.get(Note, note_id)
                        if not note or not note.content or note.ai_processed:
                            continue
                        original_content = note.content
                        processed = await ai_service.process_note(note.id, note.content, note.title)
                        db.refresh(note)
                        if processed and note.content == original_content:
                            for key, value in processed.items():
                                setattr(note, key, value)
                            note.ai_stale = 0
                            db.commit()
                            ai_done += 1
                        else:
                            ai_failed += 1
                result['ai_done'] = ai_done
                result['ai_failed'] = ai_failed
                if ai_failed:
                    result['status'] = 'partial'
            elif task.platform == "yuque":
                from app.importers.yuque import import_from_yuque
                result = await import_from_yuque(config, db)
            elif task.platform == "wechat":
                from app.importers.wechat import import_from_wechat
                result = await import_from_wechat(config, db)
            elif task.platform == "douyin":
                from app.importers.douyin import import_from_douyin
                result = await import_from_douyin(config, db)
            task.total_count = result.get("total", 0)
            task.success_count = result.get("success", 0)
            task.error_count = result.get("failed", 0)
            partial = result.get("status") == "partial" or bool(task.error_count)
            task.status = "partial" if partial else "completed"
            # Report bounded numeric details, not arbitrary importer messages.
            task.error_message = (
                f"Partial import: failed={int(task.error_count)}, "
                f"skipped={int(result.get('skipped', 0))}, "
                f"duplicates={int(result.get('duplicates', 0))}, "
                f"truncated={bool(result.get('truncated', False))}"
            ) if partial else None
            task.error_message = (task.error_message or '') + (
                f" 更新正文={int(result.get('updated',0))}，重复={int(result.get('duplicates',0))}，"
                f"未取得全文={int(result.get('incomplete',0))}，图片失败={int(result.get('image_failed',0))}，"
                f"AI完成={int(result.get('ai_done',0))}，AI失败={int(result.get('ai_failed',0))}"
            )
            task.last_sync_at = datetime.now(timezone.utc)
        except Exception as exc:
            db.rollback()
            task = db.get(ImportTask, task_id)
            task.status = "failed"
            # Third-party errors can contain HTTP headers/tokens. Never persist
            # exception text or log a traceback containing credentials.
            task.error_message = "Import failed; check server credentials, configuration and source availability"
            logger.warning("Import task %s failed (%s)", task_id, type(exc).__name__)
        updates = {"active_task_id": None}
        if task.status in ("completed", "partial"):
            updates["last_sync_at"] = task.last_sync_at
        db.query(DataSource).filter(DataSource.active_task_id == task_id).update(updates, synchronize_session=False)
        db.commit()


@router.delete("/{task_id}")
async def delete_import_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(ImportTask, task_id)
    if task is None:
        raise HTTPException(404, "Import task not found")
    if task.status in ACTIVE_STATUSES:
        raise HTTPException(409, "Cannot delete an active import task")
    db.delete(task)
    db.commit()
    return {"message": "Import task deleted successfully"}
