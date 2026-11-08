from abc import ABC, abstractmethod
from typing import List, Optional


class AIProvider(ABC):
    """AI 提供者抽象基类"""
    
    @abstractmethod
    async def generate_tags(self, content: str, title: str = "") -> List[str]:
        """
        根据内容生成标签
        
        Args:
            content: 笔记内容
            title: 笔记标题
            
        Returns:
            标签列表
        """
        pass
    
    @abstractmethod
    async def categorize(self, content: str, title: str = "") -> str:
        """
        对笔记进行分类
        
        Args:
            content: 笔记内容
            title: 笔记标题
            
        Returns:
            分类名称
        """
        pass
    
    @abstractmethod
    async def summarize(self, content: str, max_length: int = 200) -> str:
        """
        生成笔记摘要
        
        Args:
            content: 笔记内容
            max_length: 摘要最大长度
            
        Returns:
            摘要文本
        """
        pass
    
    @abstractmethod
    async def extract_title(self, content: str) -> str:
        """
        从内容中提取标题
        
        Args:
            content: 笔记内容
            
        Returns:
            提取的标题
        """
        pass
    
    @abstractmethod
    async def check_duplicate(self, content: str, existing_contents: List[str]) -> Optional[int]:
        """
        检查重复内容
        
        Args:
            content: 待检查的内容
            existing_contents: 已有内容列表
            
        Returns:
            如果发现重复，返回重复内容的索引；否则返回 None
        """
        pass


class AIFactory:
    """AI 提供者工厂类"""
    
    _providers = {}
    
    @classmethod
    def register(cls, name: str, provider_class):
        """注册 AI 提供者"""
        cls._providers[name] = provider_class
    
    @classmethod
    def create(cls, provider_name: str, **kwargs) -> AIProvider:
        """创建 AI 提供者实例"""
        if provider_name not in cls._providers:
            raise ValueError(f"Unknown AI provider: {provider_name}")
        return cls._providers[provider_name](**kwargs)
    
    @classmethod
    def list_providers(cls) -> List[str]:
        """列出所有已注册的提供者"""
        return list(cls._providers.keys())
