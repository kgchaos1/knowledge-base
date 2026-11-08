# 当前维护入口与清理原则

## 目录

- backend/app 与 backend/tests：服务端源码及正式离线回归测试。
- frontend/index.html、frontend/test-ui.cjs：网页及无需依赖的交互回归。
- obsidian-plugin/：Obsidian连接器插件源码及测试。
- obsidian_adapter/：Python导出工具（可选批量导出）。
- deploy/：统一SSH连接、版本发布、网页发布、只读冒烟。
- sync-client/：旧的ZIP备份助手，仅用于备份，不是知识库客户端。
- .tooling/：本地工具缓存，不打包源码、不提交。
- .local/：当前Windows用户DPAPI保护的SSH连接资料，不打包/提交。

## 有效命令（项目根目录）

```powershell
# 后端测试
.\.venv-test\Scripts\python.exe -B -W ignore -m unittest discover -s backend/tests -q

# 前端UI测试
node frontend/test-ui.cjs

# Obsidian插件测试
cd obsidian-plugin
npm test
npm run build

# 部署
python deploy/smoke.py
python deploy/release.py
python deploy/frontend.py
```

部署命令仅操作PKM，会先备份SQLite和附件，沿用ip-release-state.json中的版本状态，不重启Docker daemon或其它项目。只读smoke不导入、不删除、不调用AI。

## 凭据

deploy/remote.py优先使用PKM_SSH_HOST/USER/PASSWORD环境；本机可使用.local/remote.dpapi，仅当前Windows用户可解密。主机密钥必须在known_hosts中，未知主机拒绝连接。不要将凭据重新放回调试脚本。PKM-login-private.txt为用户已交付登录信息，单独保留且gitignore；不出现在安装包。

历史脚本曾存在明文SSH密码和Cookie，迁移/删除文件不等于凭据轮换，亦不等于磁盘安全擦除；未来应由用户确认轮换。线上老发布目录和备份未在本轮自动删除。

## 清理

保留正式测试，删除已被替代的一次性根目录诊断/修复/远程测试脚本。删除旧凭据脚本前必须确认统一连接可用。历史发行包是已交付文件，不为整理目录擅自移除；用户资料、数据库和备份不混入一般清理。后续只使用明确白名单打包，不用整个项目tar递归打包。
