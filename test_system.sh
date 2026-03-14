#!/bin/bash

# AI 养老健康评估系统 - 测试脚本

echo "=================================="
echo "AI 养老健康评估系统 - 功能测试"
echo "=================================="
echo ""

# 激活虚拟环境
if [ ! -d "/tmp/tiaozhanzhe-backend-venv" ]; then
    echo "❌ 虚拟环境不存在，请先运行 start_backend.sh"
    exit 1
fi

source /tmp/tiaozhanzhe-backend-venv/bin/activate
echo "✅ 虚拟环境已激活"

# 进入项目目录
cd "$(dirname "$0")"

# 检查 API Key
if grep -q "your_deepseek_api_key_here" .env 2>/dev/null; then
    echo ""
    echo "⚠️  警告：API Key 未配置，测试将会失败！"
    echo "请先编辑 .env 文件，填入真实的 DEEPSEEK_API_KEY"
    echo ""
    exit 1
fi

echo ""
echo "🧪 开始测试多 Agent 系统..."
echo ""
echo "请选择测试模式："
echo "1. 单个样本测试（快速验证）"
echo "2. 批量处理 50 条数据"
echo "3. 自定义数量"
echo ""

# 运行测试
python3 code/multi_agent_system_v2.py
