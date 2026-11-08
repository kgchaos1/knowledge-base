# PKM 独立 ZIP 备份客户端

`backup_client.py` 是 Python **标准库**客户端，提供 tkinter GUI 和 CLI；旧版 `sync.py` 保持不变，两者互不依赖。新客户端下载服务器生成的完整备份，不导出笔记、不自动解压、不触发恢复、不删除已有本地文件，不注册开机启动、服务或后台计划任务，也不安装任何软件。

## 运行

需要现有 Python 3.9+（GUI 需要该 Python 自带 tkinter；若系统没有 tkinter，可用 CLI）。Windows 普通用户可直接双击同目录的 **`start-backup.cmd`**：入口用带引号的完整路径支持中文/空格路径，先检测 `python`、再检测 `py -3` 及 tkinter，不安装任何软件。保留诊断控制台以便启动失败时看见错误；关闭 GUI 后启动器退出。首次默认目录仍为 `D:\PKM备份`。在项目根目录也可运行：

```powershell
# 默认打开 GUI，可选择/新建目标目录、输入密码、立刻同步、定时同步或停止
python sync-client/backup_client.py
python sync-client/backup_client.py --gui

# 同步一次：默认目录 D:\PKM备份，也可指定其他盘/临时目录
python sync-client/backup_client.py --once --directory "D:\PKM备份"

# 每 30 分钟同步一次，启动时立即执行一次；Ctrl+C 停止
python sync-client/backup_client.py --watch 30 --directory "E:\PKM备份"

# 推荐使用部署好 TLS 的真实地址
python sync-client/backup_client.py --server https://pkm.example.com --once
```

默认 server：`http://8.133.220.164:8091`；默认目录：`D:\PKM备份`；默认 GUI 间隔 30 分钟。保存过的设置会成为下次默认值，CLI 参数优先。`--once`、`--watch 分钟`、`--gui` 互斥。间隔必须为有限数值且至少 0.01 分钟。

### 密码和 HTTP 风险

- 每次启动需重新输入密码，CLI 使用 `getpass` 隐藏输入，GUI 密码框遮盖；无密码命令行参数，避免 shell 历史记录泄露。
- 密码和 cookie 仅在当前进程内存中；不写配置、日志、下载清单或 cookie 文件。进程结束后不再保留。Python 无法保证内存字节即时安全擦除，也不抵御系统级内存转储。
- **HTTP 明文会暴露密码、cookie 和备份内容，并允许中间人篡改文件和摘要。** GUI 每次开始需确认风险；CLI 必须输入大写 `YES` 后才提示密码并联网。摘要校验不能替代 HTTPS。拒绝重定向，避免凭证被转发；应直接填写最终服务器地址。
- 配置仅保存 `server` / `dir` / `interval`：Windows `%APPDATA%\pkm-backup\config.json`；没有 APPDATA 时为 `~/.config/pkm-backup/config.json`。该文件通过临时文件原子替换。不会保存凭证。

## 下载行为和安全边界

1. 登录取得 cookie，读取备份列表，仅考虑 `status == "completed"`。
2. 按 `created_at` 降序排列；验证本地同名文件大小和 SHA256，匹配即视为已下载。**每次只下载最新的一份尚未下载备份**；后续同步可继续获取较早的未下载备份。没有本地成功清单，因此失败不会被记为成功。
3. 严格拒绝路径、斜杠/反斜杠、盘符、ADS、Windows 设备名、控制字符及非 `.zip` 文件名。服务器应保证文件名唯一；同名不同内容会报错并保留原文件，不覆盖、不重命名旧文件。
4. 数据流写入目标目录随机 `.pkm-*.part` 文件，验证服务器声明的精确字节大小和 SHA256，flush/fsync 后原子发布为 `.zip`。使用**同目录硬链接原子、不可覆盖发布**，随后仅清理本次创建的 `.part`，防止并发任务覆盖已有文件。目标文件系统须支持硬链接（例如 NTFS、常见 Linux 文件系统）；FAT/exFAT 或部分网络盘不支持时安全失败，不降级为可覆盖写入。
5. 不解压，也不检查 ZIP 内部路径：内容验证与安全解压应在独立恢复验证环境进行。客户端不下载运行代码，不执行备份内容。
6. 401 会用内存中的密码重新登录并重试一次；错误密码不会无限重登。网络、HTTP、磁盘、元数据及校验失败均不记为成功。watch 等待完整间隔后重试，不忙轮询；单次模式失败返回非零退出码。
7. GUI 网络和磁盘校验在工作线程，界面不阻塞。停止是协作式的，当前网络读取可能需等待超时（socket 超时 15 秒）；停止完成后可再次立刻同步。关闭窗口立即结束 GUI，工作线程不会令进程后台常驻。强制关闭时可能留下 `.part`，它不会被视为已完成，客户端不会自动清理他人或上次进程的文件。
8. 不对本地目录内恶意并发改写、磁盘损坏、服务器被入侵提供完整防护；请使用受信任的本地备份目录并保留异地副本。

## 服务端 API 要求

客户端仅消费接口，不实现服务器备份生成或恢复功能。所有路径相对 `server`，`server` 必须是纯 origin：`http(s)://host[:port]`，不带账号密码、路径、查询参数或片段。请求均发送 `Origin: <server>`，使用标准库内存 CookieJar，TLS 默认执行系统证书校验。

### 登录

```http
POST /api/auth/login
Origin: http://8.133.220.164:8091
Content-Type: application/json

{"password":"用户本次输入的密码"}
```

成功返回 2xx 并通过 `Set-Cookie` 建立会话（cookie Path 应覆盖 `/api/`；HTTPS 建议 Secure/HttpOnly/SameSite），密码错误返回 401。客户端不依赖登录响应 JSON 内容，不支持 token-only 登录。

### 列表

```http
GET /api/backups/
Cookie: <登录会话>
```

成功必须返回 **JSON 数组而不是分页对象**，示例：

```json
[
  {
    "id": "backup-20261104",
    "filename": "pkm-20261104T010000Z.zip",
    "size": 123456,
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "created_at": "2026-11-04T01:00:00Z",
    "status": "completed"
  }
]
```

- `id`：非空字符串或整数，客户端 URL 编码后作为路径段。
- `filename`：全局唯一、安全 ZIP basename（建议 ASCII 字母、数字、横线、下划线和 `.zip`，最长 200 字符）。
- `size`：真实 ZIP 的非负整数字节数，不能是字符串、布尔值或解压后大小。
- `sha256`：真实 ZIP 字节的 64 位十六进制 SHA256；无摘要时客户端拒绝下载。
- `created_at`：**带时区** ISO8601，例如 `Z` 或 `+08:00`；不接受无时区日期。
- `status`：仅 `completed` 可下载；其他状态跳过。服务器仅在 ZIP 完整落盘、大小和摘要确定后设 completed，随后不可修改同一记录内容。
- 列表上限 8 MiB；无备份返回 `[]`。

### 下载

```http
GET /api/backups/{id}/download
Cookie: <登录会话>
```

成功 200 返回完整 ZIP 原始字节，推荐 `Content-Type: application/zip`、正确 `Content-Length`，不得透明改变 ZIP 字节或跳转外部下载地址。客户端使用列表 filename，不信任 Content-Disposition 中的路径。未登录/过期返回 401，失败返回适当非 2xx；下载中途失败不得仍被服务端报告为完整文件。

## 离线测试

测试仅访问 `127.0.0.1` 随机端口的内存 mock HTTP 服务，所有写入位于 `TemporaryDirectory`，**不创建或写入真实 D 盘备份目录，也不连接默认公网服务器**：

```powershell
python -m unittest discover -s sync-client -p test_backup_client.py -v
```

覆盖 HTTP 风险确认、Origin/cookie 登录、401 重登、最新 completed 选择、重复校验、失败重试、非法文件名/元数据、大小/SHA256 校验失败、已有文件和并发冲突不覆盖、取消、不忙轮询以及配置字段白名单。GUI 交互需在桌面环境手工验证。

## Restore：建议服务器独立验证

下载成功只证明文件与服务器声明的大小及摘要一致，**不证明数据库/附件完整，也不证明可恢复**。本客户端没有 restore 按钮，不自动解压、不调用恢复 API。

建议服务器提供独立的备份验证工具/只读验证接口：检查 ZIP 结构、manifest、版本兼容性、数据库完整性及附件引用一致性；解压必须拒绝路径穿越、绝对路径、符号链接及 ZIP bomb。定期把某一完整备份在隔离临时实例中恢复演练，验证笔记、附件与关键统计，再记录验证结果。

真实灾难恢复前先停止生产写入、额外备份当前数据，在空的隔离目录验证后按服务端正式恢复流程导入；确认无误再切换生产。不应直接把 ZIP 解压覆盖生产目录。ZIP 可能包含私密笔记和附件，请限制备份目录访问权限、启用磁盘加密并维护离线副本。
