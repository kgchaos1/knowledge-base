#!/bin/bash
# PKM 系统启动脚本

set -e

echo "🚀 启动 PKM 个人知识管理系统"
echo "================================"
echo ""

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装，请先安装 Docker"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose 未安装，请先安装 Docker Compose"
    exit 1
fi

# 检查 .env 文件
if [ ! -f .env ]; then
    echo "📝 创建配置文件..."
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "⚠️  请编辑 .env 文件设置密码和 API Key"
        echo ""
        read -p "按回车键继续..."
    else
        echo "❌ .env.example 文件不存在"
        exit 1
    fi
fi

# 创建必要的目录
echo "📁 创建数据目录..."
mkdir -p data backups logs

# 启动服务
echo "🐳 启动 Docker 容器..."
docker-compose up -d

# 等待服务启动
echo "⏳ 等待服务启动..."
sleep 5

# 检查服务状态
if curl -s http://localhost:8091/api/health > /dev/null 2>&1; then
    echo "✅ 服务启动成功！"
    echo ""
    echo "📍 访问地址:"
    echo "   网页管理: http://localhost:8091/"
    echo "   API 文档: http://localhost:8091/docs"
    echo "   健康检查: http://localhost:8091/api/health"
    echo ""
    echo "📖 常用命令:"
    echo "   查看日志: docker-compose logs -f"
    echo "   停止服务: docker-compose down"
    echo "   重启服务: docker-compose restart"
    echo ""
else
    echo "❌ 服务启动失败，请查看日志:"
    echo "   docker-compose logs"
    exit 1
fi
