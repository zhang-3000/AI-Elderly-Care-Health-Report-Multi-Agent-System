#!/bin/bash

# AI 养老健康评估系统 - 后端启动脚本

echo "=================================="
echo "AI 养老健康评估系统 - 后端服务"
echo "=================================="
echo ""

# 检查虚拟环境
if [ ! -d "/tmp/tiaozhanzhe-backend-venv" ]; then
    echo "❌ 虚拟环境不存在，正在创建..."
    python3 -m venv /tmp/tiaozhanzhe-backend-venv
    source /tmp/tiaozhanzhe-backend-venv/bin/activate
    pip install --upgrade pip
    pip install fastapi uvicorn pydantic python-multipart python-dotenv openpyxl pandas openai
    echo "✅ 虚拟环境创建完成"
else
    echo "✅ 虚拟环境已存在"
fi

# 激活虚拟环境
source /tmp/tiaozhanzhe-backend-venv/bin/activate
echo "✅ 虚拟环境已激活"

# 检查 API Key
if grep -q "your_deepseek_api_key_here" .env 2>/dev/null; then
    echo ""
    echo "⚠️  警告：检测到 API Key 未配置！"
    echo "请编辑 .env 文件，填入真实的 DEEPSEEK_API_KEY"
    echo ""
    echo "按 Ctrl+C 取消，或按回车继续（将会失败）..."
    read
fi

# 进入项目目录
cd "$(dirname "$0")"

echo ""
echo "🚀 启动后端服务..."
echo "访问地址：http://localhost:8000"
echo "API 文档：http://localhost:8000/docs"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

# 启动服务
python3 api/server.py
