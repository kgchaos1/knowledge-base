from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Integer, JSON, String, Table, Text,
    event, text,
)
from sqlalchemy.sql import func
from app.core.database import Base


# Kept outside notes so old INTEGER PRIMARY KEY tables need no risky rebuild.
# Backups/restores must retain this table even when every note has been deleted.
note_id_allocator = Table(
    "id_allocator", Base.metadata,
    Column("name", String(50), primary_key=True),
    Column("value", Integer, nullable=False),
    CheckConstraint("value >= 0", name="ck_id_allocator_nonnegative"),
)


class Note(Base):
    """笔记模型"""
    __tablename__ = "notes"
    # New databases also get SQLite's native protection. Existing tables keep
    # their original schema and are protected by the ORM allocator below.
    __table_args__ = {"sqlite_autoincrement": True}
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False, index=True)
    content = Column(Text, nullable=False)
    source = Column(String(50), index=True)  # yuque, wechat, douyin, zhihu, manual
    source_id = Column(String(200))  # 原平台的ID
    source_url = Column(String(1000))  # 原始链接
    tags = Column(JSON, default=list)  # ["标签1", "标签2"]
    category = Column(String(100), index=True)  # 分类
    ai_summary = Column(Text)  # AI生成的摘要
    ai_processed = Column(Integer, default=0)  # 是否已AI处理: 0=未处理, 1=已处理
    content_status = Column(String(50), nullable=False, default="unknown", server_default="unknown")
    images = Column(JSON, nullable=False, default=list, server_default="[]")
    image_total = Column(Integer, nullable=False, default=0, server_default="0")
    image_failed = Column(Integer, nullable=False, default=0, server_default="0")
    content_warning = Column(Text, nullable=True)
    ai_stale = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<Note(id={self.id}, title='{self.title[:30]}...')>"


@event.listens_for(Note, "before_insert")
def _allocate_sqlite_note_id(mapper, connection, note):
    """Reserve IDs under SQLite's transaction-scoped writer lock.

    Ordinary ORM inserts (including importers) use this path. Core/bulk inserts
    and direct SQL bypass mapper events and are deliberately not covered. Never
    replace this with a SELECT followed by an unguarded INSERT: separate workers
    must acquire the write lock *before* reading either maximum. No RETURNING or
    UPSERT syntax is used, for compatibility with older SQLite installations.
    Non-SQLite engines retain their normal generated-primary-key behavior.
    """
    if connection.dialect.name != "sqlite":
        return

    # Even when the row already exists, this write obtains the SQLite writer
    # lock. All following reads/writes and the eventual Note INSERT share the
    # same transaction, so competing workers cannot reserve the same ID.
    connection.execute(text(
        "INSERT OR IGNORE INTO id_allocator (name, value) VALUES ('note', 0)"
    ))
    connection.execute(text(
        "UPDATE id_allocator SET value = max(value, "
        "coalesce((SELECT max(id) FROM notes), 0), :explicit_id) "
        "WHERE name = 'note'"
    ), {"explicit_id": note.id if note.id is not None else 0})
    if note.id is None:
        result = connection.execute(text(
            "UPDATE id_allocator SET value = value + 1 "
            "WHERE name = 'note' AND value < 9223372036854775807"
        ))
        if result.rowcount != 1:
            # SQLite otherwise promotes overflowing integers to REAL; fail
            # closed rather than wrap, reuse an ID, or lose integer precision.
            raise OverflowError("SQLite note ID range is exhausted")
        note.id = connection.execute(text(
            "SELECT value FROM id_allocator WHERE name = 'note'"
        )).scalar_one()
    # Explicit imported IDs remain unchanged. A conflicting existing primary
    # key still fails normally; advancing the watermark is rolled back with it.


class ImportTask(Base):
    """导入任务模型"""
    __tablename__ = "import_tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(50), nullable=False, index=True)  # yuque, wechat, douyin, zhihu
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    config = Column(JSON)  # 导入配置
    total_count = Column(Integer, default=0)  # 总数
    success_count = Column(Integer, default=0)  # 成功数
    error_count = Column(Integer, default=0)  # 失败数
    error_message = Column(Text)  # 错误信息
    last_sync_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<ImportTask(id={self.id}, platform='{self.platform}', status='{self.status}')>"


class DataSource(Base):
    """Saved non-secret source settings; deliberately no foreign key to notes.

    Existing databases receive additive columns through migrate_schema;
    create_all alone does not upgrade an existing table.
    """
    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    platform = Column(String(50), nullable=False, default="zhihu")
    collection_id = Column(String(200), nullable=False)
    collection_url = Column(String(1000), nullable=False, unique=True)
    auto_ai = Column(Integer, nullable=False, default=0, server_default="0")
    sync_enabled = Column(Boolean, nullable=False, default=False, server_default="0")
    sync_interval_minutes = Column(Integer, nullable=False, default=1440, server_default="1440")
    next_sync_at = Column(DateTime(timezone=True), nullable=True)
    # Atomically claimed in the same transaction that creates an ImportTask.
    # No FK/cascade: deleting a source must never delete imported notes/tasks.
    active_task_id = Column(Integer, nullable=True)
    last_task_id = Column(Integer, nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Backup(Base):
    """备份记录模型"""
    __tablename__ = "backups"
    
    id = Column(Integer, primary_key=True, index=True)
    backup_type = Column(String(50), nullable=False)  # local, remote, oss, git
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    path = Column(String(1000))  # 备份路径
    file_size = Column(Integer)  # 文件大小(bytes)
    note_count = Column(Integer)  # 笔记数量
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    
    def __repr__(self):
        return f"<Backup(id={self.id}, type='{self.backup_type}', status='{self.status}')>"
