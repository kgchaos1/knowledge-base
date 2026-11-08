# PKM 系统文档目录

欢迎查阅 PKM 个人知识管理系统的完整文档。

## 📖 快速开始

新用户请从这里开始：

1. [README.md](../README.md) - 项目总览和快速开始
2. [QUICKSTART.md](../QUICKSTART.md) - 5分钟快速部署指南
3. [USER_GUIDE.md](../USER_GUIDE.md) - 完整使用手册

## 📋 需求与设计文档

### 需求分析
- [requirements.md](requirements.md) - 功能需求、用户角色、验收标准

### 技术设计
- [technical-design.md](technical-design.md) - 系统架构、技术栈、核心模块设计
- [detailed-design.md](detailed-design.md) - API 接口、数据模型、错误处理

### 协议规范
- [sync-protocol.md](sync-protocol.md) - 同步协议详细规范

## 🚀 部署与运维

### 部署指南
- [deployment.md](deployment.md) - 完整部署手册
  - 环境要求
  - 快速部署
  - HTTPS 配置
  - 备份恢复
  - 故障排查

### 运维指南
- [operations.md](operations.md) - 日常运维操作
  - 备份与恢复
  - 数据迁移
  - 性能调优
  - 监控告警

### 开发维护
- [maintenance.md](maintenance.md) - 开发和维护指南
  - 目录结构
  - 测试命令
  - 凭据管理
  - 代码规范

## 📦 功能文档

### 文件导入
- [file-import.md](file-import.md) - Markdown/文本文件导入说明

### 临时测试功能
- [public-sync-testing.md](public-sync-testing.md) - 免配对测试模式说明（测试环境）

## 📊 项目历史

### 开发记录
- [phase-acceptance.md](phase-acceptance.md) - 各阶段验收记录
  - 0.1 版本：基础功能
  - 0.2 版本：设备配对
  - 0.3 版本：自动同步
  - 0.4 版本：接收范围
  - 0.5 版本：检索功能
  - 插件版本：Obsidian 集成

## 🔗 外部链接

### Obsidian 插件
- [obsidian-plugin/README.md](../obsidian-plugin/README.md) - 插件安装和使用

### Python 导出工具
- [obsidian_adapter/README.md](../obsidian_adapter/README.md) - 批量导出工具（可选）

## 📌 文档阅读建议

### 我是新用户
1. 阅读 [README.md](../README.md) 了解系统概况
2. 按照 [QUICKSTART.md](../QUICKSTART.md) 快速部署
3. 参考 [USER_GUIDE.md](../USER_GUIDE.md) 学习使用

### 我要部署到生产环境
1. 阅读 [deployment.md](deployment.md) 完整部署指南
2. 特别注意 HTTPS 配置和安全加固部分
3. 配置自动备份和监控

### 我要开发和维护
1. 阅读 [technical-design.md](technical-design.md) 理解架构
2. 参考 [detailed-design.md](detailed-design.md) 了解接口
3. 查看 [maintenance.md](maintenance.md) 了解开发流程

### 我遇到问题
1. 查看 [deployment.md](deployment.md) 的故障排查章节
2. 查看 [operations.md](operations.md) 的运维指南
3. 检查 Docker 日志：`docker-compose logs -f`

## 📝 文档维护

文档更新日期：2024-11

如有文档问题或建议，请联系项目维护者。
