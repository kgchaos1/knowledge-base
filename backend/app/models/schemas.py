import re
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List, Literal
from datetime import datetime, timezone


# Note Schemas
class NoteBase(BaseModel):
    title: str = Field(..., max_length=500)
    content: str
    source: Optional[str] = "manual"
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    tags: List[str] = []
    category: Optional[str] = None


class NoteCreate(NoteBase):
    pass


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    category: Optional[str] = None


class NoteResponse(NoteBase):
    id: int
    ai_summary: Optional[str] = None
    ai_processed: int
    content_status: str = "unknown"
    images: list = Field(default_factory=list)
    image_total: int = 0
    image_failed: int = 0
    content_warning: Optional[str] = None
    ai_stale: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class NoteList(BaseModel):
    total: int
    items: List[NoteResponse]


# ImportTask Schemas
class ImportTaskCreate(BaseModel):
    platform: str = Field(..., description="yuque, wechat, douyin, zhihu")
    model_config = ConfigDict(extra="forbid")
    config: dict = Field(default_factory=dict)


class ImportTaskResponse(BaseModel):
    id: int
    platform: str
    status: str
    total_count: int
    success_count: int
    error_count: int
    error_message: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


def normalize_collection_url(value: str) -> str:
    """Validate the exact HTTPS host/path; discard copied tracking metadata."""
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ValueError("Expected a Zhihu collection URL or numeric ID")
    match = re.fullmatch(r"([0-9]{1,200})", value)
    if not match:
        # Optional trailing slash, query and fragment are never persisted. The
        # authority/path stay exact: no credentials, port, alternate host or
        # percent-encoded path can bypass validation.
        match = re.fullmatch(
            r"https://www\.zhihu\.com/collection/([0-9]{1,200})/?(?:\?[^#]*)?(?:#.*)?", value
        )
    if not match:
        raise ValueError("Use https://www.zhihu.com/collection/<digits> or digits only")
    # Canonicalize leading zeros so aliases cannot evade duplicate protection.
    collection_id = match.group(1).lstrip("0") or "0"
    return f"https://www.zhihu.com/collection/{collection_id}"


class DataSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="知乎收藏夹", min_length=1, max_length=200)
    platform: Literal["zhihu"] = "zhihu"
    collection_url: str
    auto_ai: bool = False
    sync_enabled: bool = False
    sync_interval_minutes: int = Field(default=1440, ge=60, le=10080, strict=True)

    _normalize_url = field_validator("collection_url")(normalize_collection_url)


class DataSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    collection_url: Optional[str] = None
    auto_ai: Optional[bool] = None
    sync_enabled: Optional[bool] = None
    sync_interval_minutes: Optional[int] = Field(default=None, ge=60, le=10080, strict=True)

    @field_validator("name", "collection_url", "auto_ai", "sync_enabled", "sync_interval_minutes")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null when provided")
        return value

    _normalize_url = field_validator("collection_url")(normalize_collection_url)


class DataSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    platform: str
    collection_url: str
    collection_id: str
    auto_ai: bool = False
    sync_enabled: bool = False
    sync_interval_minutes: int = 1440
    next_sync_at: Optional[datetime] = None

    @field_validator("next_sync_at")
    @classmethod
    def utc_next_sync(cls, value):
        # SQLite strips tzinfo from DATETIME; stored schedule timestamps are UTC.
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    active_task_id: Optional[int] = None
    last_task_id: Optional[int] = None
    last_sync_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# Backup Schemas
class BackupCreate(BaseModel):
    backup_type: str = Field(..., description="local, remote, oss, git")
    path: Optional[str] = None


class BackupResponse(BaseModel):
    id: int
    backup_type: str
    status: str
    path: Optional[str] = None
    file_size: Optional[int] = None
    note_count: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


# AI Processing Schemas
class AIProcessRequest(BaseModel):
    note_ids: Optional[List[int]] = None  # 如果为空，则处理所有未处理的笔记
    force: bool = False  # 是否强制重新处理已处理过的笔记


class AIProcessResponse(BaseModel):
    total: int
    processed: int
    failed: int
    message: str
