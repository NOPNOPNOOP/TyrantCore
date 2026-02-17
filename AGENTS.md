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

## 架构与模块文档

详见 `docs/` 目录：

- [architecture.md](docs/architecture.md) — 架构哲学、核心实体、通信协议、权限模型、模块依赖图
- [changelog.md](docs/changelog.md) — 版本历史 + 路线图
- `docs/modules/` — 各模块接口定义：
  - [models.md](docs/modules/models.md) — 数据模型 + Enum + 异常
  - [store.md](docs/modules/store.md) — Store ABC + SqliteStore
  - [message-bus.md](docs/modules/message-bus.md) — 消息总线
  - [event-bus.md](docs/modules/event-bus.md) — 事件总线
  - [api.md](docs/modules/api.md) — SystemAPI
  - [runtime.md](docs/modules/runtime.md) — AgentRuntime + tools + truncation
  - [cli.md](docs/modules/cli.md) — CLI 适配器
  - [terminal.md](docs/modules/terminal.md) — 终端子系统

修改任何模块前应先阅读对应的模块文档。

## 关键设计决策

- `Message.from_node` 由系统自动填充，不可伪造
- REPORTS_TO 关系通过原子 API 管理，不能单独建立
- 权限带作用域（Scope），Deny 优先；Tyrant (is_owner=True) 隐含全局权限
- Node.tags 用于服务发现，list_nodes 支持按 tags 过滤
- session_mode: persistent（默认）/ per_request（每条消息独立 session）
- ADK LlmAgent.name 必须是合法标识符（无空格）

## 约定

- 所有 ID: uuid4 字符串
- 时间: UTC datetime，存储为 ISO 8601 字符串
- bool 存储为 0/1（SQLite）
- 异步方法统一 async/await，错误通过 TyrantError 子类抛出
- 日志用 structlog，contextvars 自动绑定 node_id
- JSON 字段由 SQLAlchemy JSON 类型自动序列化
- `Message.from_node` 序列化时映射为 `"from"`
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
