import httpx
import json
from typing import List, Optional
from app.ai.base import AIProvider, AIFactory
from app.core.config import settings


class DeepSeekProvider(AIProvider):
    """DeepSeek AI 提供者"""
    
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or settings.DEEPSEEK_API_KEY
        self.base_url = base_url or settings.DEEPSEEK_BASE_URL
        self.model = model or settings.DEEPSEEK_MODEL
        
        if not self.api_key:
            raise ValueError("DeepSeek API key is required")
    
    async def _call_api(self, messages: List[dict], temperature: float = 0.7) -> str:
        """调用 DeepSeek API"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
    
    async def generate_tags(self, content: str, title: str = "") -> List[str]:
        """生成标签"""
        prompt = f"""请为以下笔记生成3-5个合适的标签。标签应该简洁、准确地概括笔记的主题和关键概念。

标题: {title}
内容: {content[:500]}...

要求:
1. 返回3-5个标签
2. 每个标签2-4个字
3. 只返回标签，用逗号分隔，不要其他解释

示例: 技术,Python,数据库,后端开发"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.5)
        
        # 解析标签
        tags = [tag.strip() for tag in response.split(",")]
        return tags[:5]  # 最多返回5个
    
    async def categorize(self, content: str, title: str = "") -> str:
        """分类"""
        prompt = f"""请为以下笔记选择一个最合适的分类。

标题: {title}
内容: {content[:500]}...

可选分类: 技术开发, 产品设计, 工作笔记, 学习笔记, 生活随笔, 阅读摘录, 灵感想法, 资源收藏, 其他

要求: 只返回分类名称，不要其他解释"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.3)
        return response.strip()
    
    async def summarize(self, content: str, max_length: int = 200) -> str:
        """生成摘要"""
        prompt = f"""请为以下笔记生成一个简洁的摘要，字数控制在{max_length}字以内。

内容: {content}

要求:
1. 提炼核心内容
2. 语言简洁明了
3. 字数不超过{max_length}字
4. 只返回摘要内容，不要其他解释"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.5)
        return response.strip()
    
    async def extract_title(self, content: str) -> str:
        """提取标题"""
        prompt = f"""请为以下笔记内容生成一个合适的标题。

内容: {content[:500]}...

要求:
1. 标题简洁明了，10-20字
2. 能准确概括内容主题
3. 只返回标题，不要其他解释"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.5)
        return response.strip()
    
    async def check_duplicate(self, content: str, existing_contents: List[str]) -> Optional[int]:
        """检查重复（简单实现，基于文本相似度）"""
        # 简单的实现：如果内容完全一致或高度相似
        content_lower = content.lower().strip()
        
        for idx, existing in enumerate(existing_contents):
            existing_lower = existing.lower().strip()
            
            # 完全一致
            if content_lower == existing_lower:
                return idx
            
            # 高度相似（简单的相似度检查）
            if len(content_lower) > 50 and len(existing_lower) > 50:
                # 计算共同子串比例
                shorter = min(len(content_lower), len(existing_lower))
                longer = max(len(content_lower), len(existing_lower))
                
                if shorter / longer > 0.95:  # 95% 相似
                    return idx
        
        return None


# 注册到工厂
AIFactory.register("deepseek", DeepSeekProvider)
