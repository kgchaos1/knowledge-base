# PKM 系统部署手册

## 1. 环境要求

### 1.1 服务器要求
- **操作系统**：Linux（Ubuntu 20.04+ 或 CentOS 7+）
- **CPU**：2 核心+
- **内存**：2GB+
- **磁盘**：20GB+（根据数据量调整）
- **网络**：公网 IP 或域名

### 1.2 软件依赖
- Docker 20.10+
- Docker Compose 1.29+
- Python 3.11+（部署脚本）
- Git（可选，用于代码管理）

### 1.3 客户端要求
- Obsidian 1.11.4+
- Windows/Mac/Linux/Android/iOS

## 2. 快速部署

### 2.1 克隆代码
```bash
git clone <repository-url> pkm-system
cd pkm-system
```

### 2.2 配置环境变量
创建 `.env` 文件：

```bash
# 基础配置
SECRET_KEY=<随机生成64字符>
ADMIN_PASSWORD=<管理员密码>

# 数据库
DATABASE_URL=sqlite:////data/pkm.db

# AI 配置（可选）
ANTHROPIC_API_KEY=<your-key>
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 网络配置
PKM_ALLOW_INSECURE_HTTP=true  # 仅测试环境
```

生成 SECRET_KEY：
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2.3 启动服务
```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f pkm-api

# 检查状态
curl http://localhost:8091/api/health
```

### 2.4 初始化数据库
数据库会在首次启动时自动创建表结构。

### 2.5 访问系统
- **网页管理**：http://your-server-ip:8091/
- **API 文档**：http://your-server-ip:8091/docs

## 3. 生产环境部署

### 3.1 HTTPS 配置

#### 准备证书
使用 Let's Encrypt 获取免费证书：

```bash
# 安装 certbot
sudo apt install certbot

# 获取证书
sudo certbot certonly --standalone -d your-domain.com
```

证书位置：
- `/etc/letsencrypt/live/your-domain.com/fullchain.pem`
- `/etc/letsencrypt/live/your-domain.com/privkey.pem`

#### 更新 Nginx 配置
编辑 `nginx.conf`：

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    # SSL 配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    
    # 其他配置...
}

# HTTP 重定向到 HTTPS
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}
```

#### 更新环境变量
```bash
# .env
PKM_ALLOW_INSECURE_HTTP=false
```

#### 证书自动续期
```bash
# 添加 cron 任务
sudo crontab -e

# 每周检查并续期
0 3 * * 1 certbot renew --quiet && docker-compose restart pkm-web
```

### 3.2 防火墙配置
```bash
# Ubuntu/Debian
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# CentOS/RHEL
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### 3.3 反向代理配置（可选）
如果使用额外的反向代理（如外部 Nginx）：

```nginx
upstream pkm_backend {
    server localhost:8091;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    # SSL 配置...
    
    location / {
        proxy_pass http://pkm_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 3.4 备份配置

#### 自动备份
系统默认每日凌晨 2 点自动备份，保留最近 7 天。

配置位置：`backend/app/core/scheduler.py`

#### 手动备份
```bash
# 通过 API
curl -X POST http://localhost:8091/api/backups/create \
  -H "Cookie: pkm_session=<your-session>"

# 或使用部署脚本
python deploy/backup.py
```

#### 备份恢复
```bash
# 1. 停止服务
docker-compose stop pkm-api

# 2. 恢复数据库
cp backups/backup-20240101-020000.db data/pkm.db

# 3. 恢复附件
tar -xzf backups/attachments-20240101-020000.tar.gz -C data/

# 4. 重启服务
docker-compose start pkm-api
```

### 3.5 监控配置

#### 健康检查
```bash
# 添加到监控系统
*/5 * * * * curl -f http://localhost:8091/api/health || echo "PKM API down"
```

#### 磁盘空间监控
```bash
# 检查磁盘使用
df -h /path/to/pkm/data

# 清理旧备份（保留30天）
find ./backups -name "*.zip" -mtime +30 -delete
```

#### 日志管理
```bash
# 查看最近日志
docker-compose logs --tail=100 pkm-api

# 持久化日志
docker-compose logs -f > logs/pkm-$(date +%Y%m%d).log &
```

## 4. 升级部署

### 4.1 版本化部署
使用提供的部署脚本：

```bash
# 1. 配置 SSH 连接
# 编辑 .env 或使用环境变量
export PKM_SSH_HOST=your-server-ip
export PKM_SSH_USER=root
export PKM_SSH_PASSWORD=<password>

# 2. 运行部署脚本
python deploy/release.py

# 3. 验证部署
python deploy/smoke.py
```

部署脚本会：
1. 备份当前数据库和附件
2. 上传新代码
3. 更新 docker-compose 配置
4. 滚动升级容器
5. 验证服务健康
6. 失败时自动回滚

### 4.2 手动升级
```bash
# 1. 拉取最新代码
git pull origin main

# 2. 备份数据
python deploy/backup.py

# 3. 重新构建并启动
docker-compose up -d --build

# 4. 验证
curl http://localhost:8091/api/health
```

### 4.3 数据库迁移
如果有数据库结构变更：

```bash
# 执行迁移脚本
docker-compose exec pkm-api python -m alembic upgrade head
```

## 5. 故障排查

### 5.1 常见问题

#### API 无法访问
```bash
# 检查容器状态
docker-compose ps

# 查看日志
docker-compose logs pkm-api

# 检查端口占用
netstat -tlnp | grep 8091

# 重启服务
docker-compose restart
```

#### 数据库损坏
```bash
# 检查数据库完整性
sqlite3 data/pkm.db "PRAGMA integrity_check;"

# 恢复备份
cp backups/latest.db data/pkm.db
docker-compose restart pkm-api
```

#### 磁盘空间不足
```bash
# 清理旧备份
find ./backups -name "*.zip" -mtime +7 -delete

# 清理 Docker 缓存
docker system prune -a

# 压缩数据库
sqlite3 data/pkm.db "VACUUM;"
```

#### 同步失败
1. 检查设备 token 是否有效
2. 检查网络连接
3. 查看 API 日志
4. 尝试重新配对

### 5.2 性能优化

#### 数据库优化
```bash
# 分析查询性能
sqlite3 data/pkm.db "EXPLAIN QUERY PLAN SELECT * FROM notes WHERE ..."

# 重建索引
sqlite3 data/pkm.db "REINDEX;"

# 优化数据库
sqlite3 data/pkm.db "PRAGMA optimize;"
```

#### 容器资源限制
编辑 `docker-compose.yml`：

```yaml
services:
  pkm-api:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 512M
```

## 6. 安全加固

### 6.1 系统安全
```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 配置自动安全更新
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

### 6.2 Docker 安全
```bash
# 使用非 root 用户
# 编辑 Dockerfile
USER pkm

# 扫描镜像漏洞
docker scan pkm-api:latest
```

### 6.3 应用安全
- 定期更新依赖包
- 使用强密码
- 定期轮换 SECRET_KEY
- 限制 API 访问 IP（如有需要）

### 6.4 网络安全
```bash
# 仅允许特定 IP 访问
# 在 nginx.conf 添加
allow 1.2.3.4;
deny all;

# 或使用防火墙
sudo ufw allow from 1.2.3.4 to any port 443
```

## 7. 开发环境部署

### 7.1 本地开发
```bash
# 安装依赖
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

# 运行开发服务器
cd backend
uvicorn app.main:app --reload --port 8000

# 运行测试
python -m pytest tests/
```

### 7.2 Obsidian 插件开发
```bash
cd obsidian-plugin

# 安装依赖
npm install

# 构建
npm run build

# 测试
npm test

# 开发模式
npm run dev
```

## 8. 配置参考

### 8.1 环境变量完整列表
```bash
# 必需
SECRET_KEY=                    # 会话加密密钥
ADMIN_PASSWORD=               # 管理员密码

# 数据库
DATABASE_URL=sqlite:////data/pkm.db

# 网络
PKM_ALLOW_INSECURE_HTTP=false
PKM_TEST_PUBLIC_SYNC=false    # 测试模式

# AI 配置（可选）
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=https://api.anthropic.com

OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 备份配置
BACKUP_KEEP_DAYS=7
BACKUP_SCHEDULE=0 2 * * *

# 日志级别
LOG_LEVEL=INFO
```

### 8.2 Docker Compose 配置
参考 `docker-compose.yml` 和 `compose.secure.yml`

### 8.3 Nginx 配置
参考 `nginx.conf`

## 9. 附录

### 9.1 端口说明
- **8091**：主服务端口（HTTP/HTTPS）
- **8000**：开发环境 API 端口

### 9.2 目录结构
```
pkm-system/
├── backend/          # 后端代码
├── frontend/         # 前端页面
├── obsidian-plugin/  # Obsidian 插件
├── deploy/           # 部署脚本
├── data/             # 数据目录（运行时生成）
│   ├── pkm.db       # SQLite 数据库
│   └── attachments/ # 图片存储
├── backups/          # 备份目录
└── logs/             # 日志目录
```

### 9.3 备份文件命名
- 数据库：`backup-YYYYMMDD-HHMMSS.db`
- 附件：`attachments-YYYYMMDD-HHMMSS.tar.gz`
- 完整备份：`backup-YYYYMMDD-HHMMSS.zip`

### 9.4 常用命令
```bash
# 启动服务
docker-compose up -d

# 停止服务
docker-compose down

# 查看日志
docker-compose logs -f

# 进入容器
docker-compose exec pkm-api bash

# 重启服务
docker-compose restart

# 更新并重启
docker-compose up -d --build

# 清理资源
docker-compose down -v
docker system prune -a
```

### 9.5 支持与反馈
遇到问题请查看：
- 项目文档：`docs/` 目录
- 故障排查：本文档第 5 节
- 日志文件：`docker-compose logs`
