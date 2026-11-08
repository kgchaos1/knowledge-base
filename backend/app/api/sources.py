"""Data source API. Mount router with prefix='/api' in the application."""
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import or_

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.import_tasks import run_import_task
from app.core.database import get_db
from app.models.note import DataSource, ImportTask
from app.models_membership import NoteSource
from app.models.schemas import DataSourceCreate, DataSourceUpdate, DataSourceResponse, ImportTaskResponse

router = APIRouter(prefix="/sources", tags=["sources"])


def get_source(db: Session, source_id: int) -> DataSource:
    source = db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(404, "Data source not found")
    return source


@router.get("/", response_model=List[DataSourceResponse])
async def list_sources(db: Session = Depends(get_db)):
    return db.query(DataSource).order_by(DataSource.id.desc()).all()


@router.post("/", response_model=DataSourceResponse, status_code=201)
async def create_source(payload: DataSourceCreate, db: Session = Depends(get_db)):
    source = DataSource(
        name=payload.name, platform=payload.platform, auto_ai=bool(payload.auto_ai),
        collection_url=payload.collection_url,
        collection_id=payload.collection_url.rsplit("/", 1)[-1],
        sync_enabled=payload.sync_enabled,
        sync_interval_minutes=payload.sync_interval_minutes,
        next_sync_at=(datetime.now(timezone.utc) + timedelta(minutes=payload.sync_interval_minutes)) if payload.sync_enabled else None,
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This collection is already saved") from None
    db.refresh(source)
    return source


@router.get("/{source_id}", response_model=DataSourceResponse)
async def read_source(source_id: int, db: Session = Depends(get_db)):
    return get_source(db, source_id)


@router.patch("/{source_id}", response_model=DataSourceResponse)
async def update_source(source_id: int, payload: DataSourceUpdate, db: Session = Depends(get_db)):
    source = get_source(db, source_id)
    values = payload.model_dump(exclude_unset=True)
    if 'auto_ai' in values and values['auto_ai'] is None:
        raise HTTPException(422, 'auto_ai must be true or false')
    if not values:
        raise HTTPException(422, "Provide name or collection_url")
    if {"sync_enabled", "sync_interval_minutes"} & values.keys():
        enabled = values.get("sync_enabled", source.sync_enabled)
        interval = values.get("sync_interval_minutes", source.sync_interval_minutes)
        values["next_sync_at"] = datetime.now(timezone.utc) + timedelta(minutes=interval) if enabled else None
    if "collection_url" in values:
        values["collection_id"] = values["collection_url"].rsplit("/", 1)[-1]
        values["last_task_id"] = None
        values["last_sync_at"] = None
    try:
        changed = db.query(DataSource).filter(
            DataSource.id == source_id, DataSource.active_task_id.is_(None)
        ).update(values, synchronize_session=False)
        if not changed:
            db.rollback()
            raise HTTPException(409, "Cannot edit a source while sync is pending or running")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This collection is already saved") from None
    db.expire_all()
    return get_source(db, source_id)


@router.delete("/{source_id}")
async def delete_source(source_id: int, db: Session = Depends(get_db)):
    get_source(db, source_id)
    deleted = db.query(DataSource).filter(
        DataSource.id == source_id, DataSource.active_task_id.is_(None)
    ).delete(synchronize_session=False)
    if not deleted:
        db.rollback()
        raise HTTPException(409, "Cannot delete a source while sync is pending or running")
    from app.models_membership import NoteSource
    db.query(NoteSource).filter(NoteSource.source_id == source_id).delete(synchronize_session=False)
    db.commit()
    return {"message": "Data source deleted; imported notes and task history are preserved"}


def claim_source_sync(db: Session, source_id: int, *, scheduled_at=None):
    """Persist task + source claim + next due in one transaction.

    Only pass IDs to workers, never this Session. SQLite's first INSERT obtains
    the writer lock before source reads; conditional UPDATE arbitrates manual
    and scheduled claims. A losing claim leaves no phantom task history.
    """
    task = ImportTask(platform="zhihu", config={}, status="pending")
    db.add(task)
    db.flush()
    query = db.query(DataSource).filter(
        DataSource.id == source_id, DataSource.active_task_id.is_(None)
    )
    if scheduled_at is not None:
        query = query.filter(DataSource.sync_enabled.is_(True), or_(
            DataSource.next_sync_at.is_(None), DataSource.next_sync_at <= scheduled_at
        ))
    claimed = query.update({"active_task_id": task.id, "last_task_id": task.id}, synchronize_session=False)
    if not claimed:
        db.rollback()
        return None
    db.expire_all()
    source = get_source(db, source_id)
    task.platform = source.platform
    task.config = {"collection_url": source.collection_url}
    if scheduled_at is not None:
        # Advance BEFORE execution, including on failure: never retry every tick.
        source.next_sync_at = scheduled_at + timedelta(minutes=source.sync_interval_minutes)
    db.commit()
    db.refresh(task)
    return task


@router.post("/{source_id}/sync", response_model=ImportTaskResponse, status_code=202)
async def sync_source(source_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    task = claim_source_sync(db, source_id)
    if task is None:
        source = get_source(db, source_id)
        raise HTTPException(409, {"message": "Sync is already pending or running", "task_id": source.active_task_id})
    background_tasks.add_task(run_import_task, task.id)
    return task
