# PKM 系统快速参考

> 一页纸快速查询常用命令和配置

## 🚀 快速启动

```bash
# 首次启动
cp .env.example .env
# 编辑 .env 设置密码
docker-compose up -d

# 检查状态
curl http://localhost:8091/api/health
```

## 📦 常用命令

### Docker 管理
```bash
docker-compose up -d          # 启动服务
docker-compose down           # 停止服务
docker-compose restart        # 重启服务
docker-compose logs -f        # 查看日志
docker-compose ps             # 查看状态
```

### 测试
```bash
# 后端测试（125项）
python -m unittest discover -s backend/tests -q

# 前端测试
node frontend/test-ui.cjs

# 插件测试（25项）
cd obsidian-plugin && npm test
```

### 部署
```bash
python deploy/smoke.py        # 冒烟测试
python deploy/release.py      # 版本化部署
python deploy/frontend.py     # 更新前端
```

## 🔑 重要端口

| 端口 | 用途 |
|------|------|
| 8091 | 主服务（HTTP） |
| 443  | HTTPS（生产环境） |

## 📍 访问地址

- 网页管理：http://localhost:8091/
- API 文档：http://localhost:8091/docs
- 健康检查：http://localhost:8091/api/health

## 🔧 环境变量

```bash
# 必需
SECRET_KEY=<64字符>           # python -c "import secrets; print(secrets.token_urlsafe(48))"
ADMIN_PASSWORD=<密码>

# 可选
ANTHROPIC_API_KEY=            # Claude API
OPENAI_API_KEY=               # OpenAI API
DEEPSEEK_API_KEY=             # DeepSeek API
```

## 📂 重要目录

```
data/pkm.db              # 数据库
data/attachments/        # 图片存储
backups/                 # 备份文件
logs/                    # 日志文件
```

## 🔒 安全检查清单

- [ ] 修改默认密码
- [ ] 生成随机 SECRET_KEY
- [ ] 配置 HTTPS（生产环境）
- [ ] 定期检查备份
- [ ] 更新系统和依赖

## 🆘 故障排查

### 服务无法启动
```bash
docker-compose ps              # 查看容器状态
docker-compose logs pkm-api    # 查看错误日志
docker system prune -a         # 清理 Docker 缓存
```

### 数据库问题
```bash
sqlite3 data/pkm.db "PRAGMA integrity_check;"  # 检查完整性
cp backups/latest.db data/pkm.db               # 恢复备份
```

### 磁盘空间
```bash
df -h                          # 查看磁盘使用
find backups -mtime +7 -delete # 清理旧备份
docker system prune -a         # 清理 Docker
```

## 📊 监控指标

```bash
# 数据库大小
du -h data/pkm.db

# 图片大小
du -sh data/attachments

# 备份大小
du -sh backups/

# 容器资源
docker stats
```

## 📝 配对流程

1. **网页端**：登录 → 设备 → 生成配对码（10分钟有效）
2. **插件端**：设置 → 填写配对码 → 配对
3. **同步**：点击「立即同步」

## 🔄 备份恢复

```bash
# 手动备份
curl -X POST http://localhost:8091/api/backups/create \
  -H "Cookie: pkm_session=<session>"

# 恢复
docker-compose stop pkm-api
cp backups/backup-YYYYMMDD.db data/pkm.db
docker-compose start pkm-api
```

## 📖 文档链接

- [完整文档](docs/INDEX.md)
- [快速开始](QUICKSTART.md)
- [使用手册](USER_GUIDE.md)
- [部署指南](docs/deployment.md)

## 🐛 调试模式

```bash
# 增加日志级别
# 在 .env 中设置
LOG_LEVEL=DEBUG

# 重启服务
docker-compose restart
```

---

**需要帮助？** 查看 [文档](docs/INDEX.md) 或检查日志 `docker-compose logs -f`
