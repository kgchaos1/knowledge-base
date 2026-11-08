from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # 应用配置
    APP_NAME: str = "PKM System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    # No authentication fallback: missing/invalid values lock API and docs.
    SECRET_KEY: str = ""  # Generate with secrets.token_urlsafe(48); minimum 32 bytes.
    PKM_PASSWORD_HASH: str = ""  # pbkdf2_sha256$600000$<salt hex>$<digest hex>
    PKM_PUBLIC_ORIGIN: str = ""  # Exact origin, no trailing slash/path/query/fragment.
    # Temporary IP-only HTTP opt-in; sends password/session over plaintext HTTP.
    # HTTPS origins always retain secure cookies and mandatory TLS.
    PKM_ALLOW_INSECURE_HTTP: bool = False
    # TEST ONLY: anonymously expose the exact device read-only sync routes.
    # Never disables admin authentication; turn off before storing private data.
    PKM_TEST_PUBLIC_SYNC: bool = False
    PKM_SESSION_TTL_SECONDS: int = 28800
    PKM_LOGIN_MAX_ATTEMPTS: int = 10
    PKM_LOGIN_WINDOW_SECONDS: int = 60
    # Comma-separated literal local/private proxy IPs, never '*', CIDRs or forwarded IPs.
    # Leave empty for direct TLS. With local TLS termination, list its real peer
    # and run uvicorn with proxy_headers=False so the ASGI peer is not rewritten.
    PKM_TRUSTED_PROXY_PEERS: str = ""

    # 数据库
    DATABASE_URL: str = "sqlite:///./data/pkm.db"

    # AI 配置
    AI_PROVIDER: str = "mock"  # deepseek / openai / local / mock

    # Anthropic compatible Messages API
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"
    ANTHROPIC_AUTH_TOKEN: str = ""
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = ""

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-3.5-turbo"

    # 本地模型
    LOCAL_MODEL_URL: str = "http://localhost:11434"
    LOCAL_MODEL_NAME: str = "qwen:7b"

    # 备份
    BACKUP_ENABLED: bool = True
    BACKUP_LOCAL_PATH: str = "./backups"
    BACKUP_SCHEDULE: str = "0 2 * * *"

    # 服务器
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:8080,http://8.133.220.164:8091"

    # 平台导入
    YUQUE_TOKEN: str = ""
    ZHIHU_COOKIE: str = ""
    DOUYIN_COOKIE: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
