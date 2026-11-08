from typing import List, Optional
from app.ai.base import AIProvider, AIFactory
import re


class MockProvider(AIProvider):
    """Mock AI 提供者，用于测试，不需要真实的 API"""
    
    async def generate_tags(self, content: str, title: str = "") -> List[str]:
        """生成模拟标签"""
        # 简单的关键词提取
        tags = []
        
        # 从标题提取
        if title:
            words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', title)
            tags.extend(words[:2])
        
        # 从内容提取常见词
        common_words = ["技术", "学习", "工作", "生活", "笔记"]
        for word in common_words:
            if word in content:
                tags.append(word)
                if len(tags) >= 5:
                    break
        
        # 如果没有标签，返回默认
        if not tags:
            tags = ["未分类"]
        
        return list(set(tags))[:5]
    
    async def categorize(self, content: str, title: str = "") -> str:
        """简单分类"""
        text = (title + " " + content).lower()
        
        if any(word in text for word in ["代码", "编程", "开发", "技术", "bug", "python", "java"]):
            return "技术开发"
        elif any(word in text for word in ["产品", "需求", "设计", "用户"]):
            return "产品设计"
        elif any(word in text for word in ["工作", "会议", "项目", "任务"]):
            return "工作笔记"
        elif any(word in text for word in ["学习", "课程", "教程", "笔记"]):
            return "学习笔记"
        elif any(word in text for word in ["阅读", "书", "文章"]):
            return "阅读摘录"
        elif any(word in text for word in ["想法", "灵感", "创意"]):
            return "灵感想法"
        else:
            return "其他"
    
    async def summarize(self, content: str, max_length: int = 200) -> str:
        """生成摘要（取前 N 个字）"""
        # 移除多余空格和换行
        cleaned = re.sub(r'\s+', ' ', content).strip()
        
        if len(cleaned) <= max_length:
            return cleaned
        
        return cleaned[:max_length] + "..."
    
    async def extract_title(self, content: str) -> str:
        """提取标题（取第一行或前 20 个字）"""
        # 尝试取第一行
        first_line = content.split('\n')[0].strip()
        
        if first_line and len(first_line) <= 50:
            return first_line[:20]
        
        # 否则取前 20 个字
        cleaned = re.sub(r'\s+', '', content)
        return cleaned[:20] + ("..." if len(cleaned) > 20 else "")
    
    async def check_duplicate(self, content: str, existing_contents: List[str]) -> Optional[int]:
        """检查重复"""
        content_cleaned = content.lower().strip()
        
        for idx, existing in enumerate(existing_contents):
            if content_cleaned == existing.lower().strip():
                return idx
        
        return None


# 注册到工厂
AIFactory.register("mock", MockProvider)
