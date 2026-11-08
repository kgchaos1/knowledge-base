# PKM 系统详细设计

## 1. API 接口设计

### 1.1 认证接口

#### POST /api/auth/login
管理员登录

**请求**：
```json
{
  "password": "string"
}
```

**响应**：
```json
{
  "authenticated": true
}
```

**说明**：
- Set-Cookie: pkm_session（HttpOnly, Secure, SameSite=Strict）
- 限流：10次/分钟（全局）
- 密码错误返回 401

#### GET /api/auth/me
检查当前会话状态

**响应**：
```json
{
  "authenticated": true
}
```

#### POST /api/auth/logout
退出登录

**响应**：
```json
{
  "authenticated": false
}
```

### 1.2 笔记接口

#### GET /api/notes/
获取笔记列表

**查询参数**：
- `page`（可选）：页码，默认 1
- `per_page`（可选）：每页数量，默认 20，最大 100
- `source_id`（可选）：数据源筛选
- `category`（可选）：分类筛选
- `tag`（可选）：标签筛选
- `search`（可选）：搜索关键词

**响应**：
```json
{
  "items": [
    {
      "id": 1,
      "title": "文章标题",
      "url": "https://...",
      "source": {
        "id": 1,
        "name": "知乎收藏夹",
        "platform": "zhihu"
      },
      "ai_summary": "摘要内容",
      "ai_tags": ["标签1", "标签2"],
      "ai_category": "技术",
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z"
    }
  ],
  "total": 100,
  "page": 1,
  "per_page": 20
}
```

#### GET /api/notes/{id}
获取笔记详情

**响应**：
```json
{
  "id": 1,
  "title": "文章标题",
  "body": "文章正文（Markdown）",
  "url": "https://...",
  "source": {
    "id": 1,
    "name": "知乎收藏夹",
    "platform": "zhihu"
  },
  "ai_summary": "摘要内容",
  "ai_tags": ["标签1", "标签2"],
  "ai_category": "技术",
  "images": [
    {
      "sha256": "abc123...",
      "url": "/api/attachments/abc123....png",
      "size": 102400
    }
  ],
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

#### DELETE /api/notes/
批量删除笔记

**请求**：
```json
{
  "ids": [1, 2, 3]
}
```

**响应**：
```json
{
  "deleted": 3
}
```

### 1.3 数据源接口

#### GET /api/sources/
获取数据源列表

**响应**：
```json
{
  "items": [
    {
      "id": 1,
      "name": "我的知乎收藏",
      "platform": "zhihu",
      "sync_enabled": true,
      "schedule": "0 2 * * *",
      "last_sync": "2024-01-01T02:00:00Z",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

#### POST /api/sources/
创建数据源

**请求**：
```json
{
  "name": "数据源名称",
  "platform": "zhihu",
  "config": {
    "collection_id": "123456",
    "cookie": "..."
  },
  "sync_enabled": false,
  "schedule": "0 2 * * *"
}
```

#### PUT /api/sources/{id}
更新数据源

#### DELETE /api/sources/{id}
删除数据源

#### POST /api/sources/{id}/sync
手动触发同步

**响应**：
```json
{
  "task_id": "abc-123",
  "status": "running"
}
```

### 1.4 文件导入接口

#### POST /api/import/
导入文件

**请求**（multipart/form-data）：
- `file`: 文件（.md, .markdown, .txt）

**响应**：
```json
{
  "imported": 1,
  "skipped": 0,
  "errors": []
}
```

### 1.5 AI 处理接口

#### POST /api/ai/process
处理笔记摘要

**请求**：
```json
{
  "note_ids": [1, 2, 3]
}
```

**响应**：
```json
{
  "processed": 3,
  "failed": 0
}
```

### 1.6 设备管理接口

#### GET /api/devices
获取设备列表

**响应**：
```json
{
  "items": [
    {
      "id": "abc123",
      "name": "Obsidian设备",
      "created_at": "2024-01-01T00:00:00Z",
      "last_seen": "2024-01-02T00:00:00Z",
      "revoked": false
    }
  ]
}
```

#### POST /api/devices/pairing
生成配对码

**请求**：
```json
{
  "name": "新设备"
}
```

**响应**：
```json
{
  "code": "ABCD-1234-EFGH-5678",
  "expires_at": "2024-01-01T00:10:00Z"
}
```

#### POST /api/devices/claim
使用配对码注册设备

**请求**：
```json
{
  "code": "ABCD-1234-EFGH-5678",
  "name": "我的设备"
}
```

**响应**：
```json
{
  "device_id": "abc123",
  "token": "device_token_here"
}
```

**说明**：
- 此接口不需要管理员认证
- 需要 Origin 检查
- 配对码单次使用

#### POST /api/devices/{id}/revoke
撤销设备

**响应**：
```json
{
  "revoked": true
}
```

### 1.7 同步接口（设备专用）

#### GET /api/sync/manifest
获取同步清单

**查询参数**：
- `scope`（可选）：all 或 sources
- `source_ids`（可选）：数据源 ID 列表（逗号分隔）

**响应**：
```json
{
  "protocol": 1,
  "snapshot": "sha256hash",
  "notes": [
    {
      "id": 1,
      "revision": "abc123"
    }
  ],
  "attachments": [
    {
      "filename": "image.png",
      "sha256": "abc123...",
      "size": 102400,
      "available": true,
      "url": "/api/attachments/abc123....png"
    }
  ],
  "scope": {
    "mode": "all",
    "source_ids": []
  }
}
```

#### GET /api/sync/sources
获取数据源列表（仅 id, name, platform）

**响应**：
```json
[
  {
    "id": 1,
    "name": "知乎收藏",
    "platform": "zhihu"
  }
]
```

#### POST /api/sync/batch
批量获取笔记

**请求**：
```json
{
  "snapshot": "sha256hash",
  "ids": [1, 2, 3],
  "scope": "all",
  "source_ids": []
}
```

**响应**：
```json
{
  "snapshot": "sha256hash",
  "notes": [
    {
      "id": 1,
      "title": "标题",
      "body": "正文",
      "url": "https://...",
      "source_id": 1,
      "platform": "zhihu",
      "ai_summary": "摘要",
      "ai_tags": ["标签"],
      "ai_category": "分类",
      "images": ["abc123..."],
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z",
      "revision": "abc123"
    }
  ]
}
```

**说明**：
- 最多 50 条/批次
- snapshot 不匹配返回 409（需重新获取清单）

#### GET /api/attachments/{sha256}.{ext}
下载图片

**响应**：二进制图片数据

### 1.8 备份接口

#### GET /api/backups/
获取备份列表

**响应**：
```json
{
  "items": [
    {
      "filename": "backup-20240101-020000.zip",
      "size": 10485760,
      "created_at": "2024-01-01T02:00:00Z",
      "checksum": "sha256hash"
    }
  ]
}
```

#### POST /api/backups/create
手动创建备份

#### GET /api/backups/{filename}
下载备份文件

#### POST /api/backups/{filename}/verify
验证备份（隔离环境）

## 2. 数据模型设计

### 2.1 Note 模型
```python
class Note(Base):
    __tablename__ = "notes"
    
    id: int  # 主键
    title: str  # 标题
    body: str  # 正文（Markdown）
    url: str  # 原始 URL（唯一）
    source_id: int  # 数据源 ID
    platform: str  # 平台标识
    ai_summary: str  # AI 摘要
    ai_tags: str  # AI 标签（JSON）
    ai_category: str  # AI 分类
    created_at: float  # 创建时间（Unix 时间戳）
    updated_at: float  # 更新时间
    deleted_at: float  # 删除时间（软删除）
    
    # 关系
    source: DataSource
    attachments: List[Attachment]
```

### 2.2 Attachment 模型
```python
class Attachment(Base):
    __tablename__ = "attachments"
    
    id: int
    sha256: str  # 内容哈希（唯一）
    filename: str  # 原始文件名
    size: int  # 文件大小
    mime_type: str  # MIME 类型
    created_at: float
```

### 2.3 DataSource 模型
```python
class DataSource(Base):
    __tablename__ = "data_sources"
    
    id: int
    name: str  # 数据源名称
    platform: str  # 平台类型（zhihu, yuque, etc）
    config: str  # 配置（JSON）
    sync_enabled: bool  # 是否启用自动同步
    schedule: str  # 定时任务（cron 表达式）
    last_sync: float  # 上次同步时间
    created_at: float
```

### 2.4 Device 模型
```python
class Device(Base):
    __tablename__ = "devices"
    
    id: str  # 32 字符 hex
    name: str  # 设备名称
    token_hash: str  # Token SHA256（唯一）
    created_at: float
    last_seen: float  # 最后访问时间
    revoked: bool  # 是否已撤销
```

## 3. Obsidian 插件设计

### 3.1 配置结构
```typescript
interface Config {
  server: string;        // 服务器地址
  root: string;          // 本地目录
  testMode: boolean;     // 测试模式
  auto: boolean;         // 自动同步
  minutes: 5|15|30|60;   // 同步间隔
  scope: 'all'|'sources'; // 接收范围
  sourceIds: number[];   // 选定数据源
  secretId: string;      // SecretStorage ID
}
```

### 3.2 核心流程

#### 配对流程
```typescript
async pair(code: string, name: string) {
  // 1. 确认 HTTP 风险
  await this.consent();
  
  // 2. 调用配对接口
  const data = await this.call('POST', '/api/devices/claim', {
    code: code.trim(),
    name: name.trim()
  }, true);
  
  // 3. 保存 token 到 SecretStorage
  this.token = data.token;
  this.config.secretId = 'pkm-' + hash;
  this.app.secretStorage.setSecret(this.config.secretId, this.token);
  
  // 4. 保存配置
  await this.save();
}
```

#### 同步流程
```typescript
async sync() {
  // 1. 获取清单
  const manifest = await this.transport.request('GET', '/api/sync/manifest');
  
  // 2. 检查本地索引
  const index = await this.vault.read(this.root + '/.pkm-index.json');
  
  // 3. 计算需要下载的笔记
  const toDownload = manifest.notes.filter(note => 
    !index[note.id] || index[note.id].revision !== note.revision
  );
  
  // 4. 批量下载笔记（每批50条）
  for (let i = 0; i < toDownload.length; i += 50) {
    const batch = toDownload.slice(i, i + 50);
    const notes = await this.transport.request('POST', '/api/sync/batch', {
      snapshot: manifest.snapshot,
      ids: batch.map(n => n.id)
    });
    
    // 5. 保存笔记
    for (const note of notes) {
      await this.saveNote(note);
    }
  }
  
  // 6. 下载图片
  for (const attachment of manifest.attachments) {
    if (attachment.available) {
      await this.downloadImage(attachment);
    }
  }
  
  // 7. 更新索引
  await this.saveIndex(manifest);
}
```

#### 冲突检测
```typescript
async saveNote(note: Note) {
  const path = `${this.root}/${note.id}.md`;
  const exists = await this.vault.exists(path);
  
  if (exists) {
    // 检查本地修改
    const stat = await this.vault.stat(path);
    const localModified = stat.mtime > this.index[note.id].synced_at;
    
    if (localModified) {
      // 保存为冲突文件
      await this.vault.write(
        `${this.root}/${note.id}.conflict-${Date.now()}.md`,
        content
      );
      return;
    }
  }
  
  // 原子写入
  await this.vault.writeAtomic(path, content);
}
```

### 3.3 文件格式
```markdown
---
title: 文章标题
url: https://...
source: 知乎收藏
platform: zhihu
tags:
  - 标签1
  - 标签2
category: 技术
created: 2024-01-01T00:00:00Z
updated: 2024-01-01T00:00:00Z
---

# 文章标题

**摘要**：AI 生成的摘要内容。

## 正文

文章正文内容...

![图片](attachments/abc123....png)

---

<details>
<summary>查看原文</summary>

原始 HTML 或 Markdown 内容...

</details>
```

## 4. 错误处理设计

### 4.1 HTTP 状态码
- **200**：成功
- **400**：请求参数错误
- **401**：未认证
- **403**：禁止访问（Origin 错误、权限不足）
- **404**：资源不存在
- **409**：冲突（快照不匹配）
- **413**：请求体过大
- **422**：验证错误
- **429**：限流
- **500**：服务器错误
- **503**：服务不可用（配置错误）

### 4.2 错误响应格式
```json
{
  "detail": "错误描述",
  "code": "ERROR_CODE",
  "extra": {}
}
```

### 4.3 客户端错误处理
```typescript
try {
  await this.sync();
} catch (error) {
  if (error.status === 401) {
    // 清除凭证，提示重新配对
    this.token = '';
    await this.clearSecret();
    new Notice('设备已撤销，请重新配对');
  } else if (error.status === 409) {
    // 快照不匹配，重新同步
    await this.sync();
  } else {
    // 其他错误
    new Notice(`同步失败：${error.message}`);
  }
}
```

## 5. 性能指标

### 5.1 响应时间目标
- 笔记列表：< 500ms
- 笔记详情：< 200ms
- 同步清单：< 1s
- 批量获取笔记（50条）：< 3s

### 5.2 并发支持
- 单用户场景，不需要高并发
- 支持同时 5 个设备同步

### 5.3 数据规模
- 笔记数量：< 10万条
- 图片总量：< 10GB
- 单笔记大小：< 1MB

## 6. 监控与日志

### 6.1 日志级别
- **ERROR**：错误和异常
- **WARNING**：警告（限流、配置问题）
- **INFO**：重要操作（登录、同步、备份）
- **DEBUG**：调试信息（开发环境）

### 6.2 关键指标
- API 响应时间
- 同步成功率
- 备份成功率
- 磁盘使用量

### 6.3 告警
- 磁盘空间 < 10%
- 备份失败
- API 持续错误 > 10次/分钟
