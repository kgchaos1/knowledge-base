# PKM 系统技术方案

## 1. 系统架构

### 1.1 整体架构

```
┌─────────────────┐
│   Obsidian      │
│   Plugin        │◄─────┐
└─────────────────┘      │
                         │ HTTPS/HTTP
┌─────────────────┐      │ (只读同步)
│   Web Browser   │◄─────┤
└─────────────────┘      │
                         │
                    ┌────▼──────┐
                    │  Nginx    │
                    │  Reverse  │
                    │  Proxy    │
                    └────┬──────┘
                         │
                    ┌────▼──────┐
                    │  FastAPI  │
                    │  Backend  │
                    └────┬──────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼─────┐    ┌────▼─────┐    ┌────▼─────┐
   │ SQLite   │    │ File     │    │ External │
   │ Database │    │ Storage  │    │ APIs     │
   └──────────┘    └──────────┘    └──────────┘
```

### 1.2 技术栈

#### 后端
- **语言**：Python 3.11+
- **Web 框架**：FastAPI 0.104+
- **数据库**：SQLite 3.x
- **ORM**：SQLAlchemy 2.x
- **异步任务**：APScheduler
- **HTTP 客户端**：httpx

#### 前端
- **网页**：纯 HTML + JavaScript（无构建）
- **样式**：内联 CSS
- **交互**：原生 Fetch API

#### Obsidian 插件
- **语言**：TypeScript
- **构建**：esbuild
- **测试**：Node.js test runner
- **API**：Obsidian Plugin API

#### 部署
- **容器化**：Docker + Docker Compose
- **反向代理**：Nginx
- **进程管理**：Docker 内置

## 2. 核心模块设计

### 2.1 认证与授权

#### 2.1.1 管理员认证
```python
# 基于会话的认证
class AuthState:
    def issue(self, now: float) -> str:
        # 生成带签名的会话 token
        # 格式: timestamp.signature
        
    def valid(self, token: str, now: float) -> bool:
        # 验证签名和过期时间
        # TTL: 8 小时
```

**安全措施**：
- 密码使用 PBKDF2-SHA256（600,000轮）
- 会话 token HMAC-SHA256 签名
- HttpOnly + Secure + SameSite=Strict Cookie
- 登录限流（10次/分钟）

#### 2.1.2 设备认证
```python
# 配对流程
def create_pairing() -> dict:
    # 生成 20 字符配对码（100位熵）
    # 存储 SHA256(code)，有效期 10 分钟
    
def claim_pairing(code: str) -> dict:
    # 验证配对码并消费
    # 生成 256 位设备 token
    # 存储 SHA256(token)
```

**设计要点**：
- 配对码单次使用
- 新配对码自动替换旧未使用码
- 设备 token 永久有效（手动撤销）
- 只读权限：仅能访问同步端点

### 2.2 同步协议

#### 2.2.1 快照机制
```python
# 快照生成
def load_snapshot(db, attachments_dir) -> Manifest:
    # 1. 查询所有笔记的 (id, updated_at, ai_summary, ...)
    # 2. 查询所有图片的 (sha256, size)
    # 3. 计算整体 SHA256 作为快照版本
    # 4. 返回 Manifest
```

**快照内容**：
- 协议版本号
- 快照 ID（SHA256）
- 笔记列表（id + revision）
- 图片列表（sha256 + url + size）
- 接收范围（all/sources + ids）

#### 2.2.2 增量同步
```
Client                          Server
  │                               │
  │  GET /api/sync/manifest       │
  ├──────────────────────────────►│
  │  {snapshot, notes, images}    │
  │◄──────────────────────────────┤
  │                               │
  │  POST /api/sync/batch         │
  │  {snapshot, ids: [1,2,3]}     │
  ├──────────────────────────────►│
  │  {notes: [...]}               │
  │◄──────────────────────────────┤
  │                               │
  │  GET /api/attachments/{hash}  │
  ├──────────────────────────────►│
  │  (binary image)               │
  │◄──────────────────────────────┤
```

**冲突处理**：
- 客户端检测本地文件修改时间
- 服务端新版本进入 `.conflict` 文件
- 保留本地修改，不覆盖

#### 2.2.3 Revision 计算
```python
def calculate_revision(note) -> str:
    # 标准化 JSON 序列化
    canonical = {
        "id": note.id,
        "title": note.title,
        "body": note.body,
        "summary": note.ai_summary,
        "tags": sorted(note.tags),
        "images": sorted(note.image_shas),
        "updated": note.updated_at.isoformat()
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True).encode()
    ).hexdigest()[:16]
```

### 2.3 数据采集

#### 2.3.1 采集架构
```python
class BaseCollector(ABC):
    @abstractmethod
    async def fetch_items(self) -> List[Item]:
        # 获取项目列表
        
    @abstractmethod
    async def fetch_content(self, item: Item) -> Content:
        # 获取详细内容
        
    async def download_images(self, urls: List[str]) -> List[bytes]:
        # 下载图片
```

#### 2.3.2 知乎收藏夹采集
```python
class ZhihuCollector(BaseCollector):
    def __init__(self, cookie: str, collection_id: str):
        self.session = httpx.AsyncClient(
            cookies=parse_cookie(cookie),
            headers={"User-Agent": "..."}
        )
        
    async def fetch_items(self):
        # 分页获取收藏夹列表
        # GET /api/collections/{id}/items
        
    async def fetch_content(self, item):
        # 获取文章详情
        # 提取正文、图片、标题
        # 清理 HTML
```

**去重机制**：
- URL 作为唯一键
- 图片 SHA256 去重
- 已存在则更新（如果 updated_at 变化）

### 2.4 AI 处理

#### 2.4.1 统一接口
```python
class AIProvider(ABC):
    @abstractmethod
    async def generate_summary(
        self, 
        title: str, 
        content: str
    ) -> Summary:
        # 生成摘要、标签、分类
```

#### 2.4.2 提供商实现
- **AnthropicProvider**：使用 Messages API
- **OpenAIProvider**：使用 Chat Completions API
- **DeepSeekProvider**：兼容 OpenAI 格式
- **MockProvider**：测试用固定返回

#### 2.4.3 Prompt 设计
```
分析以下文章，提供：
1. 一句话摘要（20-50字）
2. 3-5个标签
3. 主分类

文章标题：{title}
文章内容：{content[:4000]}

返回 JSON 格式：
{"summary": "...", "tags": [...], "category": "..."}
```

### 2.5 备份与恢复

#### 2.5.1 备份流程
```python
def create_backup():
    # 1. 停止 API 容器
    # 2. SQLite backup API 复制数据库
    # 3. tar.gz 压缩附件目录
    # 4. 生成 ZIP 包
    # 5. 重启 API 容器
```

#### 2.5.2 恢复流程
```python
def restore_backup(backup_file: str, target: str):
    # 1. 解压到临时目录
    # 2. 验证数据库完整性
    # 3. 复制到目标位置
    # 4. 重启服务
```

**隔离验证**：
- 创建临时 Docker 容器
- 挂载备份文件
- 验证数据库可读
- 不影响生产环境

## 3. 数据库设计

### 3.1 核心表结构

#### notes 表
```sql
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT UNIQUE,
    source_id INTEGER,
    platform TEXT,
    ai_summary TEXT,
    ai_tags TEXT,  -- JSON array
    ai_category TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL,
    FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE INDEX idx_notes_source ON notes(source_id);
CREATE INDEX idx_notes_updated ON notes(updated_at);
CREATE INDEX idx_notes_deleted ON notes(deleted_at);
```

#### attachments 表
```sql
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY,
    sha256 TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER,
    mime_type TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX idx_attachments_sha256 ON attachments(sha256);
```

#### note_attachments 关联表
```sql
CREATE TABLE note_attachments (
    note_id INTEGER NOT NULL,
    attachment_sha256 TEXT NOT NULL,
    PRIMARY KEY (note_id, attachment_sha256),
    FOREIGN KEY (note_id) REFERENCES notes(id),
    FOREIGN KEY (attachment_sha256) REFERENCES attachments(sha256)
);
```

#### devices 表
```sql
CREATE TABLE devices (
    id TEXT PRIMARY KEY,  -- 32字符 hex
    name TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL,
    last_seen REAL,
    revoked BOOLEAN NOT NULL DEFAULT 0
);

CREATE INDEX idx_devices_token ON devices(token_hash);
```

#### data_sources 表
```sql
CREATE TABLE data_sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    config TEXT NOT NULL,  -- JSON
    sync_enabled BOOLEAN DEFAULT 0,
    schedule TEXT,  -- cron 表达式
    last_sync REAL,
    created_at REAL NOT NULL
);
```

### 3.2 索引策略
- 频繁查询字段建立索引
- 外键关系建立索引
- 软删除字段（deleted_at）建立索引
- 避免过多索引影响写入性能

## 4. 安全设计

### 4.1 传输安全
- **生产环境**：强制 HTTPS
- **测试环境**：HTTP + 明确风险确认
- **证书**：Let's Encrypt 自动续期

### 4.2 存储安全
- **密码**：PBKDF2-SHA256（600,000轮）+ 随机盐
- **Token**：仅存储 SHA256 哈希
- **配对码**：仅存储 SHA256 哈希，10分钟过期

### 4.3 访问控制
- **管理员**：完整权限
- **设备**：只读同步端点
- **Origin 检查**：严格校验请求来源

### 4.4 限流保护
```python
class LoginLimiter:
    max_attempts = 10  # 10次
    window = 60  # 60秒
    
    # 全局计数器（进程级别）
    # 生产环境建议使用 Redis
```

## 5. 性能优化

### 5.1 数据库优化
- 使用 WAL 模式
- 批量插入使用事务
- 合理使用索引
- 定期 VACUUM

### 5.2 同步优化
- 批量获取笔记（最多50条/批次）
- 图片按需下载
- 快照缓存
- SQLite 读写分离（用事务控制）

### 5.3 前端优化
- 笔记列表分页
- 图片懒加载
- 缓存 API 响应

## 6. 测试策略

### 6.1 单元测试
- 后端：125 个测试用例
- 插件：25 个测试用例
- 覆盖核心业务逻辑

### 6.2 集成测试
- 配对流程
- 同步流程
- 冲突处理
- 设备撤销

### 6.3 端到端测试
- 真实服务器验证
- 实际插件同步
- 手动验收测试

## 7. 部署方案

### 7.1 Docker 部署
```yaml
services:
  pkm-web:
    image: nginx:alpine
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./frontend:/usr/share/nginx/html
    ports:
      - "8091:8091"
      
  pkm-api:
    image: python:3.11-slim
    volumes:
      - ./backend/app:/app
      - ./data:/data
    environment:
      - DATABASE_URL=sqlite:///data/pkm.db
    depends_on:
      - pkm-web
```

### 7.2 备份策略
- 每日自动备份（凌晨 2 点）
- 保留最近 7 天备份
- 备份内容：数据库 + 图片

### 7.3 监控告警
- API 健康检查
- 磁盘空间监控
- 备份任务监控

## 8. 技术债务与改进方向

### 8.1 当前限制
- SQLite 单文件，不适合大规模并发
- 无分布式部署支持
- 采集任务单线程执行

### 8.2 改进方向
- 迁移到 PostgreSQL
- 引入消息队列（Celery + Redis）
- 实现分布式锁
- 增加缓存层（Redis）

### 8.3 技术选型理由
- **SQLite**：部署简单，适合单用户场景
- **FastAPI**：异步支持，自动文档生成
- **Obsidian 插件**：利用成熟生态，跨平台支持
