# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

# pytest-asyncio，asyncio_mode = "auto"（无需手动标记 @pytest.mark.asyncio）
```

## 项目布局

采用 `src/` layout，包名 `tyrant_core`：

```
src/tyrant_core/
  __main__.py             # 启动入口（组装 Store → Bus → API → Runtime → CLI）
  config.py               # pydantic-settings 配置（TYRANT_* 环境变量 / .env）
  models.py               # 零依赖，纯 Pydantic 模型 + Enum + 异常
  store/                  # Store ABC + SqliteStore
  bus/                    # MessageBus + EventBus
  api/                    # SystemAPI（唯一对外入口）
  runtime/                # AgentRuntime（ADK 集成）+ file_tools + terminal_tools + truncation
  adapters/               # CLI 适配器 + TerminalSession + TerminalNode
tests/                    # pytest 测试
```

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
- **bus/message_bus.py** — 中心化消息路由，子树可达模型，内存 asyncio.Queue inbox
- **bus/event_bus.py** — 自建 pub/sub，dict pattern→callbacks，append-only 日志持久化 + replay
- **api/system_api.py** — 所有对外操作的唯一入口，权限检查在此层完成
- **runtime/agent_runtime.py** — ADK LlmAgent + Runner.run_async()，将 SystemAPI 暴露为 ADK tools，支持 persistent/per_request 两种 session 模式
- **adapters/cli.py** — Tyrant（人类）的终端交互入口，启动时自动创建预设节点（首席助手 + agent_creator）

### 核心设计决策

- `Message.from_node` 由系统自动填充，节点不可伪造（安全模型的基础）
- REPORTS_TO 关系通过原子 API（create_agent / recruit / release）管理，不能单独建立
- 权限从 bool 变为带作用域的对象列表（Scope: self_subtree / global / subtrees / nodes），Deny 优先
- Tyrant (is_owner=True) 隐含全部权限
- EventBus 不做权限检查，信任 SystemAPI 层已验证
- SQLite 单连接串行化，v1 无额外并发控制
- Node.tags 用于服务发现，list_nodes 支持按 tags 过滤
- session_mode: persistent（默认，共享 session）/ per_request（每条消息独立 session）
- clone_node 复制 agent_config + permissions + tags，生成新节点
- ADK LlmAgent.name 必须是合法标识符（字母/数字/下划线，不能有空格）

## 约定

- 所有 ID: uuid4 字符串
- 时间: UTC datetime，存储为 ISO 8601 字符串
- bool 存储为 0/1（SQLite）
- 异步方法统一 async/await，错误通过自定义异常（TyrantError 子类）抛出
- 日志用 structlog，contextvars 自动绑定 node_id 上下文
- JSON 字段（dict, list）由 SQLAlchemy JSON 类型自动序列化
- `Message.from_node` 避开 Python `from` 保留字，序列化时映射为 `"from"`
- 代码注释使用中文

## 配置

pydantic-settings 管理，配置文件 `~/.config/tyrant_core/.env`，环境变量前缀 `TYRANT_`：

- `TYRANT_MODEL` — 模型名称
- `TYRANT_API_KEY` / `TYRANT_API_BASE` — OpenAI 兼容 API
- `TYRANT_DATA_DIR` — 数据目录（默认 ./data）
- `TYRANT_LOG_LEVEL` — 日志级别（默认 INFO）
- `TYRANT_TOOL_TRUNCATE_*` / `TYRANT_MSG_TRUNCATE_*` — 截断配置
- `TYRANT_TERMINAL_*` — 终端会话配置

`config.py` 中 `get_litellm_model()` 在设置了 `api_base` 时自动添加 `openai/` 前缀；`apply_env()` 注入为 `OPENAI_API_KEY` / `OPENAI_API_BASE` 供 LiteLLM 使用。

## 规格文档

完整设计和模块规格在 `docs/` 目录：
- `docs/design.md` — 完整设计文档（架构哲学、数据模型、通信协议、权限模型）
- `docs/specs/00-overview.md` — 技术栈、项目结构、启动流程
- `docs/specs/01-07` — 各模块接口定义和实现骨架

修改任何模块前应先阅读对应的 spec 文档。
