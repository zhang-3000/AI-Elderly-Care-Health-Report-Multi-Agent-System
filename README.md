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

默认行为说明：

- macOS：安装 `corpus` + `retriever-macos`（替代依赖，避免 GPU 包）
- 非 macOS（Linux/Windows）：安装 `corpus + retriever`

### 4. 可选安装 extras

根据需要安装额外依赖：

```bash
uv sync --extra generation
uv sync --extra evaluation
uv sync --extra all
```

说明：

- `generation` 在 macOS 上会自动跳过（因为依赖 `vllm` 无 macOS 轮子）
- `all` 等价于 UltraRAG 的全部可选依赖

### 5. 常见问题

如果安装失败，多半是：

- Python 版本不匹配（务必使用 3.12）
- 平台不支持 GPU 依赖（macOS 需跳过 `generation` 与 GPU 相关包）

## 快速开始

1. 准备环境并安装依赖（见上节）
2. 根据 `docs/` 说明配置运行参数与模型 API Key
3. 运行 `code/` 下的入口脚本或对应任务流程

## 维护说明

如需更新 UltraRAG 依赖或平台兼容策略，请修改 `pyproject.toml` 中的依赖项。
建议在 Linux 环境上启用 `generation` 相关能力。
