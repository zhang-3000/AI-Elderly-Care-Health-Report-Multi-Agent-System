# AI Elderly Care Health Report Multi-Agent System

本仓库用于构建**多智能体协作**的长者健康报告系统，包含数据处理、知识检索、报告生成等能力。
当前集成了 UltraRAG 作为核心检索/语料处理组件，并通过 `uv` 管理依赖与环境。

## 目录结构

- `code/`：核心代码与多智能体逻辑
- `corpora/`：语料数据与示例文档
- `data/`：业务数据与中间文件
- `docs/`：项目文档与说明
- `result/`：报告或运行结果输出
- `系统架构与Prompt调优指南.md`：系统设计说明

## 环境与依赖（uv）

本项目使用 `uv` 管理 Python 依赖与虚拟环境。

### 1. 安装 uv

如果你还没有安装 `uv`，先执行：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> 也可以使用 `brew install uv`（macOS）或参考 uv 官方文档安装。

### 2. 创建虚拟环境（Python 3.12）

```bash
uv venv --python 3.12
source .venv/bin/activate
```

> 本项目依赖 `Python >=3.11,<3.13`，建议使用 3.12.x。

### 3. 安装默认依赖

```bash
uv sync
```

## 快速开始

### 1. 准备环境并安装依赖（见上节）

### 2. 配置环境变量

复制模板并填写必要的 API Key：

```bash
cp .env.example .env
```

编辑 `.env`，至少填写以下字段：

| 变量名 | 说明 | 是否必填 |
|--------|------|----------|
| `DEEPSEEK_API_KEY` | DeepSeek 模型 API Key | 是 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | 是 |
| `OPENAI_API_KEY` | OpenAI API Key（可选） | 否 |
| `PINECONE_API_KEY` | Pinecone 向量数据库 Key | 否 |

### 3. 启动后端服务

#### 方式一：使用启动脚本（推荐）

```bash
./start.sh
```

脚本会自动检查虚拟环境和 `.env` 配置，然后在 **8001** 端口启动服务。

#### 方式二：手动启动

```bash
source .venv/bin/activate
python api/server.py
```

启动后访问 API 文档：[http://localhost:8001/docs](http://localhost:8001/docs)

> 前端开发服务器默认会将 API 请求代理到 `http://127.0.0.1:8001`，请确保后端先于前端启动。

## 维护说明

如需更新 UltraRAG 依赖或平台兼容策略，请修改 `pyproject.toml` 中的依赖项。
建议在 Linux 环境上启用 `generation` 相关能力。
