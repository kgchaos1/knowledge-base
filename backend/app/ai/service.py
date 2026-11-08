from app.ai.base import AIFactory
from app.core.config import settings
import logging

# 导入所有 AI 提供者，确保它们被注册
from app.ai.mock import MockProvider
from app.ai.deepseek import DeepSeekProvider
from app.ai.anthropic import AnthropicProvider
from app.ai.openai import OpenAIProvider, LocalModelProvider

logger = logging.getLogger(__name__)


class AIService:
    """AI 服务管理类"""
    
    def __init__(self):
        self.provider = None
        self._load_provider()
    
    def _load_provider(self):
        """根据配置加载 AI 提供者"""
        provider_name = settings.AI_PROVIDER.lower()
        
        try:
            if provider_name == "anthropic":
                self.provider = AIFactory.create("anthropic")
            elif provider_name == "deepseek":
                self.provider = AIFactory.create("deepseek")
                logger.info("Loaded DeepSeek AI provider")
            elif provider_name == "openai":
                self.provider = AIFactory.create("openai")
                logger.info("Loaded OpenAI AI provider")
            elif provider_name == "local":
                self.provider = AIFactory.create("local")
                logger.info("Loaded Local Model AI provider")
            elif provider_name == "mock":
                self.provider = AIFactory.create("mock")
                logger.info("Loaded Mock AI provider (testing mode)")
            else:
                logger.warning(f"Unknown AI provider: {provider_name}, falling back to mock")
                self.provider = AIFactory.create("mock")
        except Exception as e:
            if provider_name == "anthropic":
                raise ValueError("Anthropic configuration is invalid; no mock fallback") from None
            logger.error(f"Failed to load AI provider {provider_name}: {e}")
            logger.info("Falling back to Mock AI provider")
            self.provider = AIFactory.create("mock")
    
    def reload_provider(self, provider_name: str = None):
        """重新加载 AI 提供者"""
        if provider_name:
            old_provider = settings.AI_PROVIDER
            settings.AI_PROVIDER = provider_name
            self._load_provider()
            if self.provider is None:
                settings.AI_PROVIDER = old_provider
                self._load_provider()
        else:
            self._load_provider()
    
    async def process_note(self, note_id: int, content: str, title: str = "", force: bool = False):
        """处理单个笔记（生成标签、分类、摘要）"""
        try:
            if not content or not content.strip():
                raise ValueError("笔记正文为空，请先补充正文后再进行 AI 整理")
            # 如果没有标题，尝试提取
            if not title or title.strip() == "":
                title = await self.provider.extract_title(content)
            
            # 生成标签
            tags = await self.provider.generate_tags(content, title)
            
            # 分类
            category = await self.provider.categorize(content, title)
            
            # 生成摘要
            summary = await self.provider.summarize(content)
            
            return {
                "title": title,
                "tags": tags,
                "category": category,
                "ai_summary": summary,
                "ai_processed": 1
            }
        except Exception as e:
            logger.error(f"Failed to process note {note_id}: {e}")
            return None
    
    async def generate_tags(self, content: str, title: str = ""):
        """生成标签"""
        return await self.provider.generate_tags(content, title)
    
    async def categorize(self, content: str, title: str = ""):
        """分类"""
        return await self.provider.categorize(content, title)
    
    async def summarize(self, content: str, max_length: int = 200):
        """生成摘要"""
        return await self.provider.summarize(content, max_length)
    
    async def extract_title(self, content: str):
        """提取标题"""
        return await self.provider.extract_title(content)
    
    def get_current_provider(self):
        """获取当前使用的提供者名称"""
        return settings.AI_PROVIDER
    
    def list_available_providers(self):
        """列出所有可用的提供者"""
        return AIFactory.list_providers()


# 全局 AI 服务实例
ai_service = AIService()
