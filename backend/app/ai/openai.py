import httpx
from typing import List, Optional
from app.ai.base import AIProvider, AIFactory
from app.core.config import settings


class OpenAIProvider(AIProvider):
    """OpenAI 兼容接口提供者（支持 OpenAI, 本地部署模型等）"""
    
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.base_url = base_url or settings.OPENAI_BASE_URL
        self.model = model or settings.OPENAI_MODEL
        
        if not self.api_key:
            raise ValueError("API key is required")
    
    async def _call_api(self, messages: List[dict], temperature: float = 0.7) -> str:
        """调用 OpenAI 兼容 API"""
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
        prompt = f"""Generate 3-5 concise tags for the following note. Tags should be brief and capture key themes.

Title: {title}
Content: {content[:500]}...

Requirements:
1. Return 3-5 tags
2. Each tag should be 2-4 words
3. Return only the tags, comma-separated, no explanation

Example: Technology,Python,Database,Backend"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.5)
        tags = [tag.strip() for tag in response.split(",")]
        return tags[:5]
    
    async def categorize(self, content: str, title: str = "") -> str:
        prompt = f"""Categorize the following note into ONE category.

Title: {title}
Content: {content[:500]}...

Categories: Technology, Product, Work, Learning, Reading, Ideas, Resources, Other

Return only the category name, no explanation."""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.3)
        return response.strip()
    
    async def summarize(self, content: str, max_length: int = 200) -> str:
        prompt = f"""Summarize the following note in {max_length} characters or less.

Content: {content}

Requirements:
1. Extract core content
2. Be concise
3. Maximum {max_length} characters
4. Return only the summary"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.5)
        return response.strip()
    
    async def extract_title(self, content: str) -> str:
        prompt = f"""Generate a concise title for the following note.

Content: {content[:500]}...

Requirements:
1. 10-20 characters
2. Capture the main theme
3. Return only the title"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self._call_api(messages, temperature=0.5)
        return response.strip()
    
    async def check_duplicate(self, content: str, existing_contents: List[str]) -> Optional[int]:
        """简单的重复检查"""
        content_lower = content.lower().strip()
        for idx, existing in enumerate(existing_contents):
            if content_lower == existing.lower().strip():
                return idx
        return None


class LocalModelProvider(AIProvider):
    """本地模型提供者（如 Ollama）"""
    
    def __init__(self, base_url: str = None, model: str = None):
        self.base_url = base_url or settings.LOCAL_MODEL_URL
        self.model = model or settings.LOCAL_MODEL_NAME
    
    async def _call_api(self, prompt: str, temperature: float = 0.7) -> str:
        """调用本地模型 API (Ollama 格式)"""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": temperature,
            "stream": False
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
    
    async def generate_tags(self, content: str, title: str = "") -> List[str]:
        prompt = f"""为以下笔记生成3-5个标签，用逗号分隔：

标题: {title}
内容: {content[:500]}...

只返回标签，不要解释："""
        
        response = await self._call_api(prompt, temperature=0.5)
        tags = [tag.strip() for tag in response.split(",")]
        return [t for t in tags if t][:5]
    
    async def categorize(self, content: str, title: str = "") -> str:
        prompt = f"""对以下笔记进行分类，从这些类别中选一个：技术开发、产品设计、工作笔记、学习笔记、阅读摘录、灵感想法、资源收藏、其他

标题: {title}
内容: {content[:500]}...

只返回分类名称："""
        
        response = await self._call_api(prompt, temperature=0.3)
        return response.strip()
    
    async def summarize(self, content: str, max_length: int = 200) -> str:
        prompt = f"""请用{max_length}字以内总结以下内容：

{content}

只返回摘要："""
        
        response = await self._call_api(prompt, temperature=0.5)
        return response.strip()
    
    async def extract_title(self, content: str) -> str:
        prompt = f"""为以下内容生成一个10-20字的标题：

{content[:500]}...

只返回标题："""
        
        response = await self._call_api(prompt, temperature=0.5)
        return response.strip()
    
    async def check_duplicate(self, content: str, existing_contents: List[str]) -> Optional[int]:
        content_lower = content.lower().strip()
        for idx, existing in enumerate(existing_contents):
            if content_lower == existing.lower().strip():
                return idx
        return None


# 注册到工厂
AIFactory.register("openai", OpenAIProvider)
AIFactory.register("local", LocalModelProvider)
