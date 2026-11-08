from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from app.core.database import get_db
from app.models.note import Note
from app.models.schemas import NoteCreate, NoteUpdate, NoteResponse, NoteList
from app.ai.service import ai_service
import logging

router = APIRouter(prefix="/notes", tags=["notes"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=NoteList)
async def list_notes(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    source: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """获取笔记列表"""
    query = db.query(Note)
    
    # 过滤条件
    if source:
        query = query.filter(Note.source == source)
    if category:
        query = query.filter(Note.category == category)
    if tag:
        # SQLite JSON 查询
        query = query.filter(Note.tags.contains(tag))
    if search:
        # 简单的全文搜索
        query = query.filter(
            (Note.title.contains(search)) | (Note.content.contains(search))
        )
    
    # 总数
    total = query.count()
    
    # 分页
    notes = query.order_by(Note.created_at.desc()).offset(skip).limit(limit).all()
    
    return {"total": total, "items": notes}


@router.get("/{note_id}", response_model=NoteResponse)
async def get_note(note_id: int, db: Session = Depends(get_db)):
    """获取单个笔记"""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.post("/", response_model=NoteResponse)
async def create_note(note: NoteCreate, db: Session = Depends(get_db)):
    """创建笔记"""
    db_note = Note(**note.model_dump())
    db.add(db_note)
    db.commit()
    db.refresh(db_note)
    return db_note


@router.put("/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: int,
    note_update: NoteUpdate,
    db: Session = Depends(get_db)
):
    """更新笔记"""
    db_note = db.query(Note).filter(Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    # 更新字段
    update_data = note_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_note, key, value)
    
    db.commit()
    db.refresh(db_note)
    return db_note


@router.delete("/{note_id}")
async def delete_note(note_id: int, db: Session = Depends(get_db)):
    """删除笔记"""
    db_note = db.query(Note).filter(Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    from app.models.note import ImportTask
    from app.api.attachments import ROOT
    import re
    from app.core.backup_service import attachment_lock
    if attachment_lock.locked():
        raise HTTPException(409, '备份正在进行，请稍后删除')
    # Single-worker route has no await below; deletion cannot interleave with lock acquisition.
    # Avoid racing attachment writes from an active import.
    if db.query(ImportTask).filter(ImportTask.status.in_(['pending', 'running'])).first():
        raise HTTPException(409, '同步正在进行，请完成后再永久删除')
    candidates = {image.get('path') for image in (db_note.images or []) if isinstance(image, dict) and isinstance(image.get('path'), str)}
    referenced = set()
    for other in db.query(Note).filter(Note.id != note_id):
        referenced.update(image.get('path') for image in (other.images or []) if isinstance(image, dict) and isinstance(image.get('path'), str))
    from app.models_membership import NoteSource
    db.query(NoteSource).filter(NoteSource.note_id == note_id).delete(synchronize_session=False)
    db.delete(db_note)
    db.commit()
    removed = 0
    failed = 0
    for filename in candidates - referenced:
        if not re.fullmatch(r'[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)', filename):
            continue
        target = ROOT / filename
        if target.parent.resolve() != ROOT.resolve() or target.is_symlink():
            continue
        try:
            if target.is_file():
                target.unlink()
                removed += 1
        except OSError:
            failed += 1
    return {'message': '笔记已永久删除', 'deleted_id': note_id, 'attachments_deleted': removed,
            'attachment_cleanup_failed': failed, 'backup_notice': '历史备份不变；再次同步来源可能重新导入'}


@router.post("/{note_id}/process")
async def process_note_ai(
    note_id: int,
    force: bool = Query(False),
    db: Session = Depends(get_db)
):
    """使用 AI 处理单个笔记"""
    db_note = db.query(Note).filter(Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    if not db_note.content or not db_note.content.strip():
        raise HTTPException(status_code=422, detail="笔记正文为空，请先补充正文再使用 AI")

    # 检查是否已处理
    if db_note.ai_processed and not force:
        return {"message": "Note already processed", "note": db_note}
    
    # AI 处理
    try:
        original_content = db_note.content
        result = await ai_service.process_note(
            note_id=note_id,
            content=db_note.content,
            title=db_note.title,
            force=force
        )
        
        if result:
            db.refresh(db_note)
            if db_note.content != original_content:
                raise HTTPException(status_code=409, detail='正文已更新，请重新整理')
            db_note.ai_stale = 0
            # 更新笔记
            for key, value in result.items():
                setattr(db_note, key, value)
            
            db.commit()
            db.refresh(db_note)
            return {"message": "Note processed successfully", "note": db_note}
        else:
            raise HTTPException(status_code=500, detail="AI processing failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing note {note_id}: {type(e).__name__}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/summary")
async def get_stats(db: Session = Depends(get_db)):
    """获取统计信息"""
    total = db.query(Note).count()
    by_source = db.query(Note.source, func.count(Note.id)).group_by(Note.source).all()
    by_category = db.query(Note.category, func.count(Note.id)).group_by(Note.category).all()
    ai_processed = db.query(Note).filter(Note.ai_processed == 1).count()
    
    return {
        "total": total,
        "by_source": dict(by_source),
        "by_category": dict(by_category),
        "ai_processed": ai_processed,
        "ai_pending": total - ai_processed
    }
