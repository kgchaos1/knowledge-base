from sqlalchemy.orm import Session
from app.models.note import Note
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


async def import_from_douyin(config: dict, db: Session) -> dict:
    """
    从抖音收藏导入笔记
    
    抖音收藏主要是视频，导入时可以记录视频标题、描述等文本信息
    
    config 参数:
    - cookie: 抖音 Cookie (可选，如果配置文件中有)
    - file_path: 手动导出的文件路径
    """
    cookie = config.get("cookie") or settings.DOUYIN_COOKIE
    file_path = config.get("file_path")
    
    # 如果提供了文件，从文件导入
    if file_path:
        return await import_from_douyin_file(file_path, db)
    
    # TODO: 实现抖音 API 抓取
    # 抖音的数据抓取比较复杂，建议使用手动导出方式
    
    logger.warning("Douyin API import is not implemented yet")
    
    return {
        "total": 0,
        "success": 0,
        "failed": 0,
        "message": "Douyin import is under development. Please use manual file import."
    }


async def import_from_douyin_file(file_path: str, db: Session) -> dict:
    """
    从抖音手动导出的文件导入
    
    用户可以手动复制抖音收藏的视频标题、描述等到文本文件
    格式示例：
    标题: 视频标题
    描述: 视频描述
    链接: https://...
    ---
    标题: 另一个视频
    ...
    """
    import os
    import re
    
    if not os.path.exists(file_path):
        raise ValueError(f"File not found: {file_path}")
    
    total = 0
    success = 0
    failed = 0
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 按分隔符分割
        items = re.split(r'\n---+\n|\n===+\n', content)
        items = [item.strip() for item in items if item.strip()]
        
        total = len(items)
        
        for item in items:
            try:
                if len(item) < 5:
                    continue
                
                # 解析字段
                title = ""
                description = ""
                url = ""
                
                lines = item.split('\n')
                for line in lines:
                    if line.startswith("标题:") or line.startswith("Title:"):
                        title = line.split(":", 1)[1].strip()
                    elif line.startswith("描述:") or line.startswith("Description:"):
                        description = line.split(":", 1)[1].strip()
                    elif line.startswith("链接:") or line.startswith("Link:") or line.startswith("URL:"):
                        url = line.split(":", 1)[1].strip()
                
                # 如果没有标题，使用描述的前 50 个字
                if not title:
                    title = (description[:50] + "...") if description else item[:50]
                
                # 组合内容
                content_text = item
                if description:
                    content_text = f"{title}\n\n{description}"
                
                note = Note(
                    title=title,
                    content=content_text,
                    source="douyin",
                    source_id=None,
                    source_url=url if url else None,
                    tags=["视频", "抖音"],
                    category=None,
                    ai_processed=0
                )
                
                db.add(note)
                db.commit()
                success += 1
                logger.info(f"Imported from Douyin: {note.title}")
                
            except Exception as e:
                failed += 1
                logger.error(f"Failed to import Douyin item: {e}")
                db.rollback()
    
    except Exception as e:
        logger.error(f"Failed to import from Douyin file: {e}")
        raise
    
    return {
        "total": total,
        "success": success,
        "failed": failed
    }
