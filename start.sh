#!/usr/bin/env bash
# -------------------------------------------------------
# start.sh — 启动 AI 养老健康助手后端服务 (端口 8001)
# -------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ---------- 虚拟环境 ----------
if [ ! -d ".venv" ]; then
    echo "❌ 未检测到虚拟环境，请先执行: uv venv --python 3.12 && uv sync"
    exit 1
fi
source .venv/bin/activate

# ---------- .env 检查 ----------
if [ ! -f ".env" ]; then
    echo "⚠️  未找到 .env 文件，正在从模板创建..."
    cp .env.example .env
    echo "请编辑 .env 填写必要的 API Key 后重新运行此脚本。"
    exit 1
fi

# ---------- 启动服务 ----------
echo "🚀 正在启动后端服务 http://0.0.0.0:8001 ..."
exec python api/server.py
