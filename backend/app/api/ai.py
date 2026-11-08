from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.note import Note
from app.models.schemas import AIProcessRequest, AIProcessResponse
from app.ai.service import ai_service
import logging

router = APIRouter(prefix="/ai", tags=["ai"])
logger = logging.getLogger(__name__)


@router.post("/process", response_model=AIProcessResponse)
async def process_notes(
    request: AIProcessRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """批量处理笔记"""
    
    # 获取待处理的笔记
    query = db.query(Note)
    
    if request.note_ids:
        # 处理指定的笔记
        query = query.filter(Note.id.in_(request.note_ids))
    elif not request.force:
        # 只处理未处理的笔记
        query = query.filter(Note.ai_processed == 0)
    
    notes = query.all()
    total = len(notes)
    
    if total == 0:
        return AIProcessResponse(
            total=0,
            processed=0,
            failed=0,
            message="No notes to process"
        )
    
    # 添加后台任务
    background_tasks.add_task(
        process_notes_background,
        notes=notes,
        db=db,
        force=request.force
    )
    
    return AIProcessResponse(
        total=total,
        processed=0,
        failed=0,
        message=f"Started processing {total} notes in background"
    )


async def process_notes_background(notes, db: Session, force: bool):
    """后台处理笔记"""
    processed = 0
    failed = 0
    
    for note in notes:
        try:
            result = await ai_service.process_note(
                note_id=note.id,
                content=note.content,
                title=note.title,
                force=force
            )
            
            if result:
                # 更新笔记
                for key, value in result.items():
                    setattr(note, key, value)
                db.commit()
                processed += 1
                logger.info(f"Processed note {note.id}")
            else:
                failed += 1
                logger.error(f"Failed to process note {note.id}")
        except Exception as e:
            failed += 1
            logger.error(f"Error processing note {note.id}: {e}")
    
    logger.info(f"Batch processing completed: {processed} succeeded, {failed} failed")


@router.get("/providers")
async def list_providers():
    """列出可用的 AI 提供者"""
    return {
        "current": ai_service.get_current_provider(),
        "available": ai_service.list_available_providers()
    }


@router.post("/providers/{provider_name}")
async def switch_provider(provider_name: str):
    """切换 AI 提供者"""
    try:
        ai_service.reload_provider(provider_name)
        return {
            "message": f"Switched to {provider_name}",
            "current": ai_service.get_current_provider()
        }
    except Exception as e:
        logger.error(f"Failed to switch provider: {e}")
        return {
            "error": str(e),
            "current": ai_service.get_current_provider()
        }


@router.post("/test")
async def test_ai():
    """测试 AI 功能"""
    test_content = "这是一篇关于Python编程的学习笔记，主要介绍了FastAPI框架的使用方法和最佳实践。"
    
    try:
        tags = await ai_service.generate_tags(test_content, "Python学习笔记")
        category = await ai_service.categorize(test_content, "Python学习笔记")
        summary = await ai_service.summarize(test_content)
        
        return {
            "status": "success",
            "provider": ai_service.get_current_provider(),
            "result": {
                "tags": tags,
                "category": category,
                "summary": summary
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "provider": ai_service.get_current_provider(),
            "error": str(e)
        }
