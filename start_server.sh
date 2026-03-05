#!/bin/bash
# 启动 FastAPI 服务器

echo "=== AI 养老健康助手 - 后端服务器 ==="
echo ""

# 检查 .env 文件是否存在
if [ ! -f ".env" ]; then
    echo "警告: .env 文件不存在"
    echo "请复制 .env.example 为 .env 并配置您的 API 密钥"
    echo ""
    echo "运行: cp .env.example .env"
    echo "然后编辑 .env 文件添加您的 DEEPSEEK_API_KEY"
    echo ""
    exit 1
fi

# 加载环境变量
set -a
source .env
set +a

echo "配置信息:"
echo "  - 主机: ${HOST:-0.0.0.0}"
echo "  - 端口: ${PORT:-8000}"
echo "  - API 地址: ${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
echo ""

# 确保依赖已安装
echo "检查依赖..."
if ! uv run python -c "import fastapi" 2>/dev/null; then
    echo "FastAPI 未安装，正在安装依赖..."
    uv sync
fi

echo ""
echo "启动服务器..."
echo "API 文档: http://localhost:${PORT:-8000}/docs"
echo ""

# 启动服务器
cd api && uv run python -m uvicorn server:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000} --reload
