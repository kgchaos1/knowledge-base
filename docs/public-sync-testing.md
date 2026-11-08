# 临时免配对同步测试

用户已确认当前仅测试且无重要数据，暂时允许 Obsidian 不输入配对码同步。

## 范围

服务端 `PKM_TEST_PUBLIC_SYNC=true` 仅对以下无 Authorization 的请求免身份验证：

- GET /api/sync/manifest
- GET /api/sync/sources
- POST /api/sync/batch（读取笔记，无服务器内容写入）
- GET /api/attachments/严格 SHA256 图片名

管理网页、普通笔记 API、导入、AI、备份、设备管理仍需管理员登录。传输和非 GET 的精确 Origin 校验保留。响应禁止缓存，并附 `X-PKM-Test-Public-Sync: true`。错误/撤销凭证不会降级成匿名访问；测试插件不发送旧凭证。

**这会让知道公网地址的人读取全部同步笔记、来源名称和图片。Origin 不是访问密码，不能阻止其他客户端。测试结束前不要存敏感内容。**

## 开启和关闭

默认代码配置为关闭。版本化部署工具保留现有环境，可显式修改：

```powershell
.\.venv-test\Scripts\python.exe deploy/release.py --test-public-sync on
.\.venv-test\Scripts\python.exe deploy/validate_public_sync.py
# 恢复正常设备鉴权
.\.venv-test\Scripts\python.exe deploy/release.py --test-public-sync off
```

本次已启用并部署到 `/root/pkm-system/releases/20261106-175204`；部署前已备份数据库和图片。真实匿名清单、来源、批量正文与图片哈希验收通过，管理入口仍返回 401。

运行部署需要已配置 SSH 凭证和 paramiko。关闭服务端测试模式后，在插件设置关闭“免配对测试模式”，使用新配对码恢复安全存储凭证。不要只关插件开关而忘记关闭服务端。

## 客户端

Obsidian 插件 0.1.2 默认开启免配对测试模式，隐藏配对表单；直接点击立即同步。原配对代码保留供测试结束恢复，不会删除已有笔记。既有 Python 导出工具或 Flutter 客户端本次未改成免配对。
