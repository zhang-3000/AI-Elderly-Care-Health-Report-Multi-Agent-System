#!/bin/bash

# 停止旧的后端服务
echo "停止旧的后端服务..."
pkill -f "python3 api/server.py"
sleep 2

# 进入项目目录
cd ~/Desktop/tiaozhanzhe/AI-Elderly-Care-Health-Report-Multi-Agent-System

# 激活虚拟环境
source /tmp/tiaozhanzhe-backend-venv/bin/activate

# 临时禁用代理（因为代理返回 403）
unset HTTP_PROXY
unset HTTPS_PROXY
unset ALL_PROXY
unset http_proxy
unset https_proxy
unset all_proxy

# 加载环境变量
export $(cat .env | grep -v '^#' | xargs)

echo "启动后端服务..."
nohup python3 api/server.py > /tmp/backend.log 2>&1 &

sleep 3

# 检查服务是否启动成功
if curl -s http://localhost:8000/api/health > /dev/null; then
    echo "✓ 后端服务启动成功"
    echo "  访问地址: http://localhost:8000"
    echo "  日志文件: /tmp/backend.log"
else
    echo "✗ 后端服务启动失败"
    echo "  查看日志: tail -f /tmp/backend.log"
    exit 1
fi
