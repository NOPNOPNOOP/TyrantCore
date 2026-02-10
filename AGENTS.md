# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## 语言

请使用中文与用户交互。

## 项目概述

TyrantCore 是一个通信/关系网络基础设施，支持以人为核心、动态扩展的 AI Agent 组织。它是**基础设施层**（造公路和交通规则），Agent 自行决定行为。核心实体：Node（可通信对象）、Relation（连接）、Message（通信）、Task（工作追踪）、Event（可观测性）。

## 构建与运行

```bash
# 包管理使用 uv
uv sync                          # 安装依赖（含 dev 依赖）
uv run python -m tyrant_core     # 运行（启动 CLI 交互）

# 测试
uv run pytest                    # 运行全部测试
uv run pytest tests/test_store.py            # 单个文件
uv run pytest tests/test_store.py::test_xxx  # 单个测试

# pytest-asyncio 用于异步测试，asyncio_mode = "auto"
```

## 项目布局

采用 `src/` layout，包名 `tyrant_core`：

```
src/tyrant_core/          # 源码
  __main__.py             # 启动入口（组装 Store → Bus → API → Runtime → CLI）
  config.py               # pydantic-settings 配置（ASA_* 环境变量 / .env）
  models.py               # 零依赖，纯 Pydantic 模型 + Enum + 异常
  store/                  # Store ABC + SqliteStore
  bus/                    # MessageBus + EventBus
  api/                    # SystemAPI（唯一对外入口）
  runtime/                # AgentRuntime（ADK 集成）
  adapters/               # CLI 适配器
tests/                    # pytest 测试（75 passed）
```

## 技术栈

- Python 3.12+, asyncio 单进程单 event loop
- google-adk (Agent 框架, Runner + SessionService 模式)
- litellm (非 Gemini 模型适配，ADK 通过 LiteLLM 调用 OpenAI 兼容 API)
- SQLAlchemy 2.0 async + aiosqlite (Core Table 定义, 非 ORM Mapped class)
- Pydantic v2 (数据模型) + pydantic-settings (配置管理)
- rich (CLI 输出) + prompt_toolkit (CLI 异步输入)
- structlog (日志, contextvars 绑定上下文)

## 架构

### 模块依赖图（上层依赖下层，不反向）

```
cli_adapter ──→ system_api
                   │
         ┌─────────┼─────────┐
         ▼         ▼         ▼
   message_bus  event_bus  agent_runtime
         │         │         │
         ▼         ▼         ▼
        store     store    message_bus
         │                 event_bus
         ▼                 adk
       models
```

实现顺序遵循此依赖图：models → store → bus → api → runtime → cli。

### 关键模块职责

- **models.py** — 零依赖，纯 Pydantic 模型 + Enum + 异常定义
- **store/** — 抽象 Store ABC + SqliteStore 实现。Pydantic ↔ SQLAlchemy 转换在 Store 内部完成
- **bus/message_bus.py** — 中心化消息路由，子树可达模型（直接关系或祖先-后代链），内存 asyncio.Queue inbox
- **bus/event_bus.py** — 自建 pub/sub（~100-150 行），dict pattern→callbacks，append-only 日志持久化 + replay
- **api/system_api.py** — 所有对外操作的唯一入口，权限检查在此层完成
- **runtime/agent_runtime.py** — ADK LlmAgent + Runner.run_async()，将 SystemAPI 暴露为 ADK tools
- **adapters/cli.py** — Tyrant（人类）的终端交互入口

### 核心设计决策

- `Message.from_node` 由系统自动填充，节点不可伪造（安全模型的基础）
- REPORTS_TO 关系通过原子 API（create_agent / recruit / release）管理，不能单独建立
- 权限作用域固定为节点自身子树，Tyrant (is_owner=True) 拥有全部权限
- EventBus 不做权限检查，信任 SystemAPI 层已验证
- 事务用 SQLAlchemy async session 的 `begin()`，SQLite 单连接串行化，v1 无额外并发控制

## 约定

- 所有 ID: uuid4 字符串
- 时间: UTC datetime，存储为 ISO 8601 字符串
- bool 存储为 0/1（SQLite）
- 异步方法统一 async/await，错误通过自定义异常（TyrantError 子类）抛出
- 日志用 structlog，contextvars 自动绑定 node_id 上下文
- 配置通过 pydantic-settings，支持环境变量（ASA_* 前缀）和 `~/.config/tyrant_core/.env` 文件
- JSON 字段（dict, list）由 SQLAlchemy JSON 类型自动序列化
- `Message.from_node` 避开 Python `from` 保留字，序列化时映射为 `"from"`

## 配置

使用 pydantic-settings 管理配置，配置文件位于 `~/.config/tyrant_core/.env`：

```bash
ASA_MODEL=claude-sonnet-4-5-20250929   # 模型名称
ASA_API_KEY=sk-xxx                     # API 密钥
ASA_API_BASE=https://api.example.com/v1  # OpenAI 兼容 API 地址
```

`config.py` 中的 `Settings` 类：
- 环境变量前缀 `ASA_`，支持 .env 文件和环境变量
- `get_litellm_model()`: 当设置了 `api_base` 时自动添加 `openai/` 前缀
- `apply_env()`: 将配置注入为 `OPENAI_API_KEY` / `OPENAI_API_BASE` 环境变量供 LiteLLM 使用
- 额外配置: `log_level`（默认 INFO）、`data_dir`（默认 `./data`）

## 规格文档

完整设计和模块规格在 `docs/` 目录：
- `docs/design.md` — 完整设计文档（架构哲学、数据模型、通信协议、权限模型）
- `docs/specs/00-overview.md` — 技术栈、项目结构、启动流程、依赖清单
- `docs/specs/01-07` — 各模块的接口定义、实现骨架和数据流

修改任何模块前应先阅读对应的 spec 文档。

## 实现进度

v1 全部实现完成（75 tests passed）。详细任务追踪见 [`docs/TASKLIST.md`](docs/TASKLIST.md)。
