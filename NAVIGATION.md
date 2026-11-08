# 📚 PKM 系统 - 文档导航

> 快速找到你需要的文档

## 🎯 我想...

### 快速上手
- **5分钟部署** → [QUICKSTART.md](QUICKSTART.md)
- **了解项目** → [README.md](README.md)
- **查看交付内容** → [DELIVERY.md](DELIVERY.md)

### 使用系统
- **完整使用指南** → [USER_GUIDE.md](USER_GUIDE.md)
- **快速参考卡片** → [REFERENCE.md](REFERENCE.md)
- **Obsidian 插件** → [obsidian-plugin/README.md](obsidian-plugin/README.md)

### 部署运维
- **完整部署手册** → [docs/deployment.md](docs/deployment.md)
- **HTTPS 配置** → [docs/deployment.md#31-https-配置](docs/deployment.md#31-https-配置)
- **备份恢复** → [docs/deployment.md#34-备份配置](docs/deployment.md#34-备份配置)
- **故障排查** → [docs/deployment.md#5-故障排查](docs/deployment.md#5-故障排查)
- **运维指南** → [docs/operations.md](docs/operations.md)

### 开发维护
- **技术架构** → [docs/technical-design.md](docs/technical-design.md)
- **API 设计** → [docs/detailed-design.md](docs/detailed-design.md)
- **维护说明** → [docs/maintenance.md](docs/maintenance.md)
- **同步协议** → [docs/sync-protocol.md](docs/sync-protocol.md)

### 了解需求
- **功能需求** → [docs/requirements.md](docs/requirements.md)
- **项目总结** → [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)
- **开发历史** → [docs/phase-acceptance.md](docs/phase-acceptance.md)

## 📂 按类型查找

### 用户文档
| 文档 | 用途 | 阅读时间 |
|------|------|----------|
| [README.md](README.md) | 项目总览、快速开始 | 5 分钟 |
| [QUICKSTART.md](QUICKSTART.md) | 5分钟部署指南 | 5 分钟 |
| [USER_GUIDE.md](USER_GUIDE.md) | 完整使用手册 | 20 分钟 |
| [REFERENCE.md](REFERENCE.md) | 快速参考卡片 | 2 分钟 |

### 技术文档
| 文档 | 用途 | 阅读时间 |
|------|------|----------|
| [docs/requirements.md](docs/requirements.md) | 功能需求和验收标准 | 15 分钟 |
| [docs/technical-design.md](docs/technical-design.md) | 系统架构和技术栈 | 30 分钟 |
| [docs/detailed-design.md](docs/detailed-design.md) | API 接口和数据模型 | 45 分钟 |
| [docs/sync-protocol.md](docs/sync-protocol.md) | 同步协议规范 | 20 分钟 |

### 运维文档
| 文档 | 用途 | 阅读时间 |
|------|------|----------|
| [docs/deployment.md](docs/deployment.md) | 完整部署手册 | 30 分钟 |
| [docs/operations.md](docs/operations.md) | 日常运维指南 | 20 分钟 |
| [docs/maintenance.md](docs/maintenance.md) | 开发维护说明 | 15 分钟 |

### 功能文档
| 文档 | 用途 | 阅读时间 |
|------|------|----------|
| [docs/file-import.md](docs/file-import.md) | 文件导入说明 | 5 分钟 |
| [docs/public-sync-testing.md](docs/public-sync-testing.md) | 测试模式说明 | 5 分钟 |
| [obsidian-plugin/README.md](obsidian-plugin/README.md) | 插件使用说明 | 10 分钟 |

### 项目文档
| 文档 | 用途 | 阅读时间 |
|------|------|----------|
| [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) | 项目交付总结 | 10 分钟 |
| [DELIVERY.md](DELIVERY.md) | 交付清单 | 5 分钟 |
| [docs/phase-acceptance.md](docs/phase-acceptance.md) | 各阶段验收记录 | 10 分钟 |

## 🔍 按场景查找

### 场景 1：我是新用户
1. 阅读 [README.md](README.md) 了解项目
2. 按照 [QUICKSTART.md](QUICKSTART.md) 部署
3. 参考 [USER_GUIDE.md](USER_GUIDE.md) 使用

### 场景 2：我要部署到生产
1. 阅读 [docs/deployment.md](docs/deployment.md)
2. 配置 HTTPS 和安全加固
3. 设置自动备份和监控

### 场景 3：我要开发功能
1. 阅读 [docs/technical-design.md](docs/technical-design.md)
2. 参考 [docs/detailed-design.md](docs/detailed-design.md)
3. 查看 [docs/maintenance.md](docs/maintenance.md)

### 场景 4：我遇到问题
1. 查看 [REFERENCE.md](REFERENCE.md) 快速参考
2. 参考 [docs/deployment.md](docs/deployment.md) 故障排查
3. 查看日志：`docker-compose logs -f`

### 场景 5：我要了解需求
1. 阅读 [docs/requirements.md](docs/requirements.md)
2. 查看 [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)
3. 参考 [docs/phase-acceptance.md](docs/phase-acceptance.md)

## 🗂️ 完整文档树

```
pkm-system/
├── README.md                    # 项目总览
├── QUICKSTART.md                # 快速开始
├── USER_GUIDE.md                # 使用手册
├── REFERENCE.md                 # 快速参考
├── PROJECT_SUMMARY.md           # 项目总结
├── DELIVERY.md                  # 交付清单
├── NAVIGATION.md                # 本文档
│
├── docs/
│   ├── INDEX.md                 # 文档目录
│   ├── requirements.md          # 功能需求
│   ├── technical-design.md      # 技术方案
│   ├── detailed-design.md       # 详细设计
│   ├── deployment.md            # 部署手册
│   ├── operations.md            # 运维指南
│   ├── maintenance.md           # 维护说明
│   ├── sync-protocol.md         # 同步协议
│   ├── file-import.md           # 文件导入
│   ├── public-sync-testing.md   # 测试模式
│   └── phase-acceptance.md      # 验收记录
│
├── obsidian-plugin/
│   └── README.md                # 插件说明
│
├── obsidian_adapter/
│   └── README.md                # 导出工具
│
├── deploy/
│   └── README-security.md       # 安全部署
│
└── sync-client/
    └── README-backup.md         # 备份助手
```

## 💡 提示

- 📌 文档中的超链接可直接点击跳转
- 🔍 使用 Ctrl+F 在文档中搜索关键词
- 📝 遇到问题先查看 [故障排查](docs/deployment.md#5-故障排查)
- 🚀 生产部署务必阅读 [安全加固](docs/deployment.md#6-安全加固)

## 📞 需要帮助？

- 📖 查看完整文档：[docs/INDEX.md](docs/INDEX.md)
- 🆘 故障排查：[docs/deployment.md#5-故障排查](docs/deployment.md#5-故障排查)
- 📝 快速参考：[REFERENCE.md](REFERENCE.md)

---

**最后更新**：2024-11  
**文档版本**：1.0
