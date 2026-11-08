# 运维状态与备份恢复预演（阶段 5）

## 安全边界与接入

由主应用把 `app.api.operations.router` 以 `/api` 前缀挂载；本模块不自行创建应用、不改变生命周期、不绕过现有全局认证。所有接口只允许现有单用户管理员会话访问；设备 Bearer 凭证不允许访问 operations。POST 同样必须符合全局 HTTPS / 精确 Origin 策略。不要把这个 router 挂到未安装 `install_auth` 的公网应用。

本轮**没有一键覆盖恢复接口**，不接受用户文件系统路径、外部 URL 或上传 ZIP，不访问网络。错误为固定结构 `{"detail":{"code":"…","message":"…"}}`，不回传异常文本、服务器绝对路径、Cookie、任务配置、笔记正文或 manifest 文件名清单。响应通过全局认证设置 `Cache-Control: no-store`。

## GET `/api/operations/status`

只读状态，不触发备份、导入、清理、全文件扫描或文件哈希校验。返回：

```json
{
  "disk": {"total": 1000000, "used": 400000, "free": 600000},
  "notes_count": 10,
  "attachments": {"references_count": 12, "unique_count": 7},
  "failed_imports_count": 2,
  "backup": {"running": false, "last_error": null},
  "source_scheduler": {"enabled_count": 3, "active_count": 1}
}
```

- `disk` 为数据库所在磁盘的字节数值，不是某个目录大小；没有路径字段。磁盘 reserved-space 的平台语义可能导致 total 不严格等于 used + free。
- `notes_count` 为全部笔记行数；`failed_imports_count` 为状态为 `failed` 的任务行数，不是失败文章数量。
- `attachments` 仅遍历数据库 `Note.images` 列。只接受字典中严格匹配 `[0-9a-f]{64}\.(jpg|jpeg|png|gif|webp)` 的 `path`。`references_count` 为有效本地引用出现次数（重复引用重复计数），`unique_count` 为去重名称数；不是附件磁盘文件数，也不保证引用的文件仍存在。远程 URL、不合法路径与非数组元数据不计数。不会加载笔记正文或任务配置。
- `backup.running` 只输出布尔状态；`last_error` 只允许 `null` 或固定的 `Backup did not complete`，绝不原样回显 service.STATE 错误字符串。
- `source_scheduler.enabled_count` 为启用定时同步的来源数；`active_count` 为具有 active_task_id 的来源数，包含已领取/运行中任务。不是内部线程数，也不证明调度循环正在运行。多个字段为独立只读查询，任务并发推进时不保证原子快照。
- 状态暂不可用时返回 503 / `operations_unavailable`，不返回底层 SQLite 或磁盘错误内容。

成本为读取 images 列、行计数与去重引用集合；随数据库笔记/引用量增长，但不会读取巨大附件文件内容。

## POST `/api/operations/backups/{backup_id}/rehearse`

`backup_id` 必须是现有备份服务生成的 32 位小写十六进制 UUID（`uuid4().hex`）。仅通过 `backup_service.lookup` 定位已登记备份；不以用户输入拼接任意目录、不接收 URL。不存在/非法 ID 返回 404 / `backup_not_found`。

实际步骤：

1. 对服务登记的归档名做 basename、目录边界、常规文件及非符号链接检查；受限复制归档到私有 `TemporaryDirectory`，确保验证与后续读取使用同一份稳定副本。
2. 重用 `verify_archive`。其完整临时数据库完整性检查、条目/解压限额、清单精确匹配、SQLite schema/count 检查、附件内容哈希及内容寻址名称校验全部仍然执行。
3. 从已验证归档**只**按固定条目名 `pkm.db` 流式复制到临时文件；再次对比大小/哈希，不使用 `extract` / `extractall`。
4. 以 SQLite `mode=ro&immutable=1`、`trusted_schema=OFF`、`query_only=ON` 打开临时源数据库，执行完整性检查和 `SELECT count(*) FROM notes`。
5. 使用 SQLite backup API 把这个临时源恢复到另一份**临时**数据库；再只读打开目标并执行完整性与计数验证。所有连接关闭后自动清理临时目录。

返回：

```json
{
  "verified": true,
  "notes_count": 10,
  "files_count": 8,
  "sqlite_sample_read": true,
  "sqlite_restore_verified": true,
  "live_data_modified": false
}
```

`files_count` 是 manifest 中的数据文件数（含 pkm.db 与附件，不含 manifest.json）。预演会实际进行一次临时 SQLite backup/还原与读回；**它不是线上真实恢复，不切换线上数据库，不把附件写入线上目录，不运行应用迁移，也不证明跨版本应用兼容**。附件仅由原有 verifier 完整哈希验证，不做附件目录切换。没有 Cookie/设备授权恢复；原备份服务已经清理快照中的设备授权及历史敏感任务配置。

归档复制/验证/数据库预演失败统一返回 422 / `rehearsal_failed`；备份清单读取失败返回 503 / `backup_unavailable`。任何错误都不包含 verifier 异常原文。预演会读取并哈希整个受限归档，可能占用 IO 和临时空间；仅由管理员按需调用，不要高频轮询。它不会更新 service.STATE 或任何线上记录，也不会为此次预演生成新备份。

## 手动恢复 runbook（仅人工操作，不是本 API 功能）

1. 确认备份来源、时间和版本，先执行上述预演，记录计数与预期业务范围。预演通过不替代对备份来源的信任。
2. **停止 PKM 服务**及后台备份/来源同步任务，确保没有持有数据库、WAL 或附件的进程；禁止在线覆盖 SQLite。
3. **先备份 current**：完整保留当前数据库及必要 WAL/SHM、当前附件和部署配置，记下原版本与权限。此离线回退副本必须放在独立位置，避免被覆盖。
4. 把选定归档复制到独立隔离工作区并重新验证。仅向独立临时位置恢复数据库与已验证附件；不要直接解压到线上目录，不运行归档中任何脚本。
5. 验证临时数据库 `PRAGMA integrity_check`、notes 行数、schema/应用版本兼容性、引用附件哈希与清单一致性；用隔离实例做抽样人工核对，不调用真实远程导入、不发送 Cookie。重新配置凭据并重新配对设备，而不是复活旧设备授权。
6. **验证后才切换**：在服务仍停止的前提下，将已经验证的数据库和匹配附件作为同一恢复集切换到部署的数据位置；避免混用旧 WAL/SHM，按部署平台手册处理 SQLite 辅助文件与权限，不盲目删除未归档文件。
7. 启动一个 PKM worker，检查健康/认证、笔记与附件抽样及运维状态；最后恢复定时任务。失败时再次停止服务，使用步骤 3 的 current 回退副本回滚。保留原副本直到验收完成。

## 测试

`python -B -m unittest discover -s backend/tests -p test_operations.py -v`

测试在独立临时工作区创建 SQLite 与归档；直接安装真实全局认证并挂载新 router，不修改父应用。覆盖未登录、Origin、设备凭证拒绝、状态计数/过滤/脱敏、GET 不校验归档、不递归扫描、成功临时恢复、归档前后 live 差异、live 数据库字节不变、非法 ID 不进入 lookup、不存在/污染元数据/损坏 ZIP/内部错误固定响应。没有网络请求、线上路径或真实凭据。
