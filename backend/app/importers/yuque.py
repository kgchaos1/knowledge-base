import httpx
from sqlalchemy.orm import Session
from app.models.note import Note
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


async def import_from_yuque(config: dict, db: Session) -> dict:
    """
    从语雀导入笔记
    
    config 参数:
    - token: 语雀 API token (可选，如果配置文件中有)
    - namespace: 知识库路径，如 "username/repo"
    """
    token = config.get("token") or settings.YUQUE_TOKEN
    namespace = config.get("namespace")
    
    if not token:
        raise ValueError("Yuque token is required")
    if not namespace:
        raise ValueError("Yuque namespace is required")
    
    base_url = "https://www.yuque.com/api/v2"
    headers = {
        "X-Auth-Token": token,
        "Content-Type": "application/json"
    }
    
    total = 0
    success = 0
    failed = 0
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 获取知识库文档列表
            response = await client.get(
                f"{base_url}/repos/{namespace}/docs",
                headers=headers
            )
            response.raise_for_status()
            docs = response.json().get("data", [])
            
            total = len(docs)
            logger.info(f"Found {total} documents in Yuque")
            
            for doc in docs:
                try:
                    # 获取文档详情
                    doc_slug = doc.get("slug")
                    detail_response = await client.get(
                        f"{base_url}/repos/{namespace}/docs/{doc_slug}",
                        headers=headers
                    )
                    detail_response.raise_for_status()
                    doc_detail = detail_response.json().get("data", {})
                    
                    # 检查是否已存在
                    existing = db.query(Note).filter(
                        Note.source == "yuque",
                        Note.source_id == str(doc_detail.get("id"))
                    ).first()
                    
                    if existing:
                        logger.info(f"Document {doc_detail.get('title')} already exists, skipping")
                        success += 1
                        continue
                    
                    # 创建笔记
                    note = Note(
                        title=doc_detail.get("title", "Untitled"),
                        content=doc_detail.get("body", ""),
                        source="yuque",
                        source_id=str(doc_detail.get("id")),
                        source_url=f"https://www.yuque.com/{namespace}/{doc_slug}",
                        tags=[],
                        category=None,
                        ai_processed=0
                    )
                    
                    db.add(note)
                    db.commit()
                    success += 1
                    logger.info(f"Imported: {note.title}")
                    
                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to import document {doc.get('slug')}: {e}")
                    db.rollback()
    
    except Exception as e:
        logger.error(f"Failed to fetch Yuque documents: {e}")
        raise
    
    return {
        "total": total,
        "success": success,
        "failed": failed
    }
