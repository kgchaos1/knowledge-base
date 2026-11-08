# Markdown / text 文件导入（阶段 3）

## API 与挂载

路由模块 `backend/app/api/file_import.py` 导出 `router`，自身前缀为 `/file-import`。由应用入口按现有方式挂载到 `/api`，沿用全局认证；模块不另建免认证路由、不启动后台任务。

两个接口均使用 `multipart/form-data`：

- `POST /api/file-import/preview`：必填字段 `file`。
- `POST /api/file-import/commit`：必填字段 `file`，可选普通文本字段 `title`（最多 500 字符）。空白 title 回退为自动标题。

仅接受 `.md`、`.markdown`、`.txt`（扩展名不区分大小写），UTF-8 或 UTF-8 BOM；文件内容最多 **2 MiB = 2,097,152 字节**，BOM 也计入上传限额。空文本、纯空白文本、NUL、非法 UTF-8 均拒绝。

### 预览

```json
{
  "filename": "笔记.md",
  "title": "中文标题",
  "chars": 18,
  "format": "markdown",
  "images_remote_warning": false,
  "content_hash": "64位小写十六进制SHA256"
}
```

`filename` 仅保留去掉 Windows/POSIX 目录前缀后的 basename，始终只是展示元数据，绝不当成本地文件路径。自动标题取首个 `# 一级标题`（支持最多 3 个前导空格，去掉可选末尾闭合 #），否则取不含扩展名的 basename；不解释 HTML、YAML/frontmatter 或模板。自动标题最多保留 500 字符。

`format` 为 `.md`/`.markdown` 对应 `markdown`、`.txt` 对应 `text`。`chars` 为移除首个 UTF-8 BOM 后正文的 Python Unicode 字符数，原换行保留。`images_remote_warning` 是保守提示：正文同时出现 Markdown/HTML 图像标记及 HTTP(S)/协议相对远程链接时为 true；不承诺解析所有 Markdown 方言，也不表示已下载图片。预览不创建笔记。

### 提交

```json
{
  "status": "imported",
  "note_id": 123,
  "title": "中文标题",
  "characters": 18
}
```

- 直接创建 `Note`：`source='file'`、`source_id=content_hash`、`content_status='full'`、`ai_processed=0`、图片列表为空。
- `full` 仅表示**上传文件的正文已完整导入**，不表示任何网站全文已经获取。
- 正文按 UTF-8 解码后原样存储（仅移除编码 BOM）；不清理 HTML、不渲染 Markdown、不提取 frontmatter、不执行其中任何内容。
- 内容身份为 SHA-256：移除编码 BOM，CRLF/CR 标准化为 LF 后重新 UTF-8 编码计算。不 trim 空白、不做 Unicode NFC、不按文件名或 title 去重。不同换行/BOM 的同一文本复用已有文件笔记。
- 已有 `source='file'` + 同 hash 的笔记时返回 `status='duplicate'` 及已有 id、标题、正文字符数，不覆盖既有内容或用户编辑、不触发 AI。
- 一次成功新建仅调用一次 `db.commit()`，通过请求依赖 `get_db` 使用独立 Session。重复请求不提交；数据库错误 rollback，响应不回显内容或 SQL 参数。
- 本阶段不改模型，不新增数据库唯一索引：常规重复请求幂等；跨进程严格并发唯一性需要以后增加约束。现有单进程 async 路由在数据库查询至 commit 间无 await。

## AI 与来源边界

本阶段不接受/执行 `auto_ai`，不调用 AI、不创建费用任务。后续如提供 AI 选项，必须明确提示并取得用户对费用的确认，由统一处理链路实现，不能隐式因文件上传调用模型。

本阶段**不创建 DataSource，也不创建 NoteSource 关联**。文件笔记在“全部笔记”（不带来源筛选）中可见；底层 `source` 值为 `file`。仅列已保存 DataSource 的 UI 不会自动出现一个“文件来源”，父页面需说明此行为（或明确支持 `source=file` 的类型筛选），不能假称其属于某个现有收藏夹。

## 安全边界与错误

- 业务代码每次读取最多 64 KiB，并最多读到限额 + 1 字节；不信任 Content-Length 或 UploadFile.size。
- 不将上传写入持久文件、不解压、不执行 metadata/HTML、不访问用户给出的文件路径、URL 或图片；上传流最终关闭。
- FastAPI/Starlette 的 multipart 解析器可能在路由调用前把上传暂存到框架管理的临时 spooled 文件；应用不复制/持久化该上传。部署反向代理仍应配置总请求体限额，防止路由解析前的 multipart 资源消耗。
- 返回错误只给固定原因，不回显正文、秘密、解析片段或数据库异常。
- 413：超过 2 MiB；415：不支持扩展名；422：非法文本/空内容/不合法标题/缺失 file；500：数据库导入失败（通用错误）。
- 内容允许含 HTML，但只是笔记文本；客户端渲染仍需遵守既有 HTML 清洗及远程资源策略。服务端没有任何远程下载行为。

## 离线回归

在 backend 目录执行：

```console
python -m unittest discover -s tests -p test_file_import.py -v
```

覆盖真实 TestClient multipart bytes、UTF-8 BOM、中文标题、Windows/POSIX basename、2 MiB 边界、超限流读取、NUL/非法 UTF-8/空文本、秘密不回显、frontmatter/模板/HTML 不执行、远程图片不下载、AI 不调用、内容原样存储、hash 去重、DB 错误 rollback。
