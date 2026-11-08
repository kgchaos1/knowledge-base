# PKM 个人知识管理系统

> 一个用于采集、整理和管理个人知识的系统，支持多平台内容采集、AI 智能处理和 Obsidian 离线同步。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-blue.svg)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-Private-red.svg)]()

## ✨ 核心特性

### 🔄 多平台采集
- **知乎收藏夹**：自动采集收藏文章，保存正文和图片
- **文件导入**：支持 Markdown/文本文件批量导入
- **增量更新**：智能去重，仅更新变化内容

### 🤖 AI 增强
- **智能摘要**：自动生成文章摘要
- **标签提取**：AI 自动提取关键标签
- **分类整理**：智能分类管理
- **多模型支持**：Anthropic Claude、OpenAI、DeepSeek、本地 Ollama

### 📱 Obsidian 同步
- **跨平台支持**：Windows/Mac/Linux/Android/iOS
- **增量同步**：只传输变化内容，节省流量
- **冲突保护**：本地修改不会被覆盖
- **离线阅读**：完整的本地副本，随时访问
- **图片本地化**：所有图片本地存储，SHA256 去重

### 🔐 安全可靠
- **设备管理**：配对码机制，安全便捷
- **权限隔离**：管理端和阅读设备权限分离
- **自动备份**：每日备份数据库和图片
- **加密存储**：密码 PBKDF2 加密，token 哈希存储

## 📋 系统要求

### 服务器
- Linux 服务器（Ubuntu 20.04+ / CentOS 7+）
- Docker 20.10+ 和 Docker Compose 1.29+
- 2 核 CPU，2GB 内存，20GB 磁盘

### 客户端
- Obsidian 1.11.4+
- 支持 Windows、Mac、Linux、Android、iOS

## 🚀 快速开始

### 1. 部署服务器

```bash
# 克隆代码
git clone <repository-url> pkm-system
cd pkm-system

# 配置环境变量
cp .env.example .env
# 编辑 .env，设置 SECRET_KEY 和 ADMIN_PASSWORD

# 启动服务
docker-compose up -d

# 检查服务状态
curl http://localhost:8091/api/health
```

### 2. 安装 Obsidian 插件

1. 下载最新版本插件：`PKM-Obsidian-Connector-X.X.X.zip`
2. 解压到 Obsidian Vault 的 `.obsidian/plugins/pkm-connector/` 目录
3. 在 Obsidian 设置中启用「PKM Connector」插件
4. 配置服务器地址并同步

详细步骤参见 [Obsidian 插件使用指南](obsidian-plugin/README.md)

### 3. 使用系统

#### 网页管理
访问 `http://your-server:8091`，使用管理员密码登录：
- 查看和管理笔记
- 配置数据源和定时同步
- 导入 Markdown 文件
- 触发 AI 处理
- 管理设备和备份

#### Obsidian 同步
1. 在网页管理端「设备」生成配对码
2. 在 Obsidian 插件中填写配对码并配对
3. 点击「立即同步」获取笔记
4. 支持自动定时同步（可选）

## 📚 文档

> 💡 **快速导航**：[NAVIGATION.md](NAVIGATION.md) - 按场景快速找到需要的文档

### 快速开始
- [快速开始指南](QUICKSTART.md) - 5分钟部署上手
- [快速参考卡片](REFERENCE.md) - 常用命令速查
- [用户使用手册](USER_GUIDE.md) - 完整功能说明

### 核心文档
- [项目交付总结](PROJECT_SUMMARY.md) - 功能、架构、测试总览
- [交付清单](DELIVERY.md) - 完整交付内容和验收
- [Obsidian 插件](obsidian-plugin/README.md) - 插件安装和使用

### 技术文档
- [需求文档](docs/requirements.md) - 功能需求和验收标准
- [技术方案](docs/technical-design.md) - 系统架构和技术栈
- [详细设计](docs/detailed-design.md) - API 接口和数据模型
- [同步协议](docs/sync-protocol.md) - 同步协议规范

### 部署运维
- [部署手册](docs/deployment.md) - 完整部署、HTTPS、备份、故障排查
- [运维指南](docs/operations.md) - 日常运维操作
- [维护说明](docs/maintenance.md) - 开发和维护指南

### 完整文档索引
- [文档目录](docs/INDEX.md) - 所有文档的组织结构
- [文档导航](NAVIGATION.md) - 按场景快速查找

## 🏗️ 项目结构

```
pkm-system/
├── backend/              # FastAPI 后端
│   ├── app/             # 应用代码
│   │   ├── api/        # API 路由
│   │   ├── core/       # 核心功能
│   │   ├── models/     # 数据模型
│   │   └── importers/  # 采集器
│   └── tests/          # 单元测试
├── frontend/            # 网页管理界面
│   ├── index.html      # 单页应用
│   └── test-ui.cjs     # UI 测试
├── obsidian-plugin/     # Obsidian 插件
│   ├── src/            # 插件源码
│   ├── test/           # 插件测试
│   └── manifest.json   # 插件元数据
├── obsidian_adapter/    # Python 导出工具（可选）
├── deploy/              # 部署脚本
│   ├── release.py      # 版本化部署
│   ├── smoke.py        # 冒烟测试
│   └── remote.py       # SSH 连接
├── docs/                # 文档
├── data/                # 数据目录（运行时）
│   ├── pkm.db          # SQLite 数据库
│   └── attachments/    # 图片存储
└── backups/             # 备份目录（运行时）
```

## 🧪 开发和测试

### 后端测试
```bash
# 安装依赖
python -m venv .venv-test
source .venv-test/bin/activate  # Windows: .venv-test\Scripts\activate
pip install -r backend/requirements.txt

# 运行测试（125 项）
python -m unittest discover -s backend/tests -q
```

### 前端测试
```bash
# 无需安装依赖
node frontend/test-ui.cjs
```

### Obsidian 插件测试
```bash
cd obsidian-plugin

# 安装依赖
npm install

# 运行测试（25 项）
npm test

# 构建插件
npm run build
```

### 部署测试
```bash
# 冒烟测试（只读）
python deploy/smoke.py

# 版本化部署
python deploy/release.py

# 更新前端
python deploy/frontend.py
```

## 🔧 配置说明

### 环境变量

创建 `.env` 文件：

```bash
# 必需配置
SECRET_KEY=<64字符随机字符串>    # 会话加密密钥
ADMIN_PASSWORD=<管理员密码>      # 登录密码

# 数据库
DATABASE_URL=sqlite:////data/pkm.db

# 网络配置
PKM_ALLOW_INSECURE_HTTP=true     # 测试环境可用 HTTP
PKM_TEST_PUBLIC_SYNC=false       # 免配对测试模式

# AI 配置（可选）
ANTHROPIC_API_KEY=<your-key>
OPENAI_API_KEY=<your-key>
DEEPSEEK_API_KEY=<your-key>
```

生成 SECRET_KEY：
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 数据源配置

知乎收藏夹示例：
```json
{
  "platform": "zhihu",
  "collection_id": "123456789",
  "cookie": "your-zhihu-cookie"
}
```

## 🛡️ 安全说明

### 当前状态
- ⚠️ 默认使用 HTTP（明文传输），仅用于公开内容测试
- ⚠️ 单用户系统，无多用户隔离
- ✅ 密码使用 PBKDF2-SHA256 加密存储
- ✅ 设备 token 使用 SHA256 哈希存储
- ✅ 支持只读设备权限

### 生产环境建议
1. **启用 HTTPS**：使用 Let's Encrypt 获取免费证书
2. **定期备份**：确保自动备份正常运行
3. **强密码**：使用复杂的管理员密码
4. **防火墙**：限制服务器访问端口
5. **定期更新**：及时更新系统和依赖包

详见 [部署手册 - 安全加固](docs/deployment.md#6-安全加固)

## 📊 技术栈

### 后端
- **Python 3.11+** - 编程语言
- **FastAPI** - Web 框架
- **SQLite** - 数据库
- **SQLAlchemy** - ORM
- **APScheduler** - 定时任务
- **httpx** - HTTP 客户端

### 前端
- **纯 HTML/CSS/JavaScript** - 无构建依赖
- **Fetch API** - 网络请求

### Obsidian 插件
- **TypeScript** - 编程语言
- **esbuild** - 构建工具
- **Obsidian API** - 插件接口

### 部署
- **Docker** - 容器化
- **Docker Compose** - 编排
- **Nginx** - 反向代理

## 🗺️ 发展路线

### 已完成 ✅
- [x] 知乎收藏夹采集
- [x] AI 智能处理（摘要、标签、分类）
- [x] 网页管理界面
- [x] 文件导入
- [x] Obsidian 插件同步
- [x] 设备配对管理
- [x] 自动备份恢复
- [x] 增量同步协议
- [x] 冲突保护机制

### 进行中 🚧
- [ ] HTTPS 生产部署
- [ ] 语雀、微信采集器
- [ ] 性能优化

### 计划中 📋
- [ ] 更多平台支持（微博、豆瓣等）
- [ ] 语义搜索
- [ ] 笔记间链接分析
- [ ] 双向编辑支持
- [ ] 多设备冲突合并

## ⚠️ 已知限制

- **单用户系统**：暂不支持多用户
- **HTTP 部署**：生产环境需自行配置 HTTPS
- **单向同步**：仅从服务器到 Obsidian，不支持反向同步
- **部分平台未完成**：语雀、微信、抖音采集器尚未完成

## 📄 许可证

本项目为私有项目，未开源。

## 🙏 致谢

- [FastAPI](https://fastapi.tiangolo.com/) - 现代化的 Python Web 框架
- [Obsidian](https://obsidian.md/) - 强大的知识管理工具
- [SQLite](https://sqlite.org/) - 轻量级数据库
- [Docker](https://www.docker.com/) - 容器化平台

---

**维护者**：Personal PKM Team  
**最后更新**：2024-11
