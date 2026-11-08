from sqlalchemy.orm import Session
from app.models.note import Note
import os
import re
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


async def import_from_wechat(config: dict, db: Session) -> dict:
    """
    从微信收藏导入笔记
    
    微信收藏需要手动导出，支持两种格式：
    1. HTML 文件（微信 PC 端导出）
    2. 文本文件（手动复制的文本）
    
    config 参数:
    - file_path: 导出文件路径
    - file_type: 文件类型 "html" 或 "text"
    """
    file_path = config.get("file_path")
    file_type = config.get("file_type", "html")
    
    if not file_path:
        raise ValueError("file_path is required")
    
    if not os.path.exists(file_path):
        raise ValueError(f"File not found: {file_path}")
    
    total = 0
    success = 0
    failed = 0
    
    try:
        if file_type == "html":
            result = await _import_from_html(file_path, db)
        elif file_type == "text":
            result = await _import_from_text(file_path, db)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
        
        return result
    
    except Exception as e:
        logger.error(f"Failed to import from WeChat: {e}")
        raise


async def _import_from_html(file_path: str, db: Session) -> dict:
    """从 HTML 文件导入"""
    total = 0
    success = 0
    failed = 0
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        soup = BeautifulSoup(html_content, "html.parser")
        
        # 查找所有收藏项（根据微信导出的 HTML 结构调整）
        # 这里是一个通用的解析逻辑，实际可能需要根据微信的导出格式调整
        items = soup.find_all("div", class_=re.compile("item|favorite|msg"))
        
        if not items:
            # 如果没有找到特定结构，尝试按段落分割
            items = soup.find_all("p")
        
        total = len(items)
        
        for item in items:
            try:
                # 提取标题和内容
                title_tag = item.find(["h1", "h2", "h3", "strong", "b"])
                title = title_tag.get_text(strip=True) if title_tag else ""
                
                content = item.get_text(strip=True)
                
                if not content or len(content) < 10:
                    continue
                
                # 如果没有标题，使用内容前 20 个字
                if not title:
                    title = content[:20] + ("..." if len(content) > 20 else "")
                
                # 创建笔记
                note = Note(
                    title=title,
                    content=content,
                    source="wechat",
                    source_id=None,
                    source_url=None,
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
                logger.error(f"Failed to import item: {e}")
                db.rollback()
    
    except Exception as e:
        logger.error(f"Failed to parse HTML file: {e}")
        raise
    
    return {
        "total": total,
        "success": success,
        "failed": failed
    }


async def _import_from_text(file_path: str, db: Session) -> dict:
    """从文本文件导入"""
    total = 0
    success = 0
    failed = 0
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 按空行分割笔记
        notes_content = re.split(r'\n\s*\n', content)
        notes_content = [n.strip() for n in notes_content if n.strip()]
        
        total = len(notes_content)
        
        for note_content in notes_content:
            try:
                if len(note_content) < 10:
                    continue
                
                # 提取标题（第一行）
                lines = note_content.split('\n')
                title = lines[0][:50] if lines else note_content[:20]
                
                # 创建笔记
                note = Note(
                    title=title,
                    content=note_content,
                    source="wechat",
                    source_id=None,
                    source_url=None,
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
                logger.error(f"Failed to import note: {e}")
                db.rollback()
    
    except Exception as e:
        logger.error(f"Failed to read text file: {e}")
        raise
    
    return {
        "total": total,
        "success": success,
        "failed": failed
    }
