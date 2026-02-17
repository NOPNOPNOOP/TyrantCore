# 00 - 实现概览

## 技术栈

- **语言**: Python 3.12+
- **Agent 框架**: google-adk（Runner + SessionService 模式）
- **异步**: asyncio（单进程，单 event loop）
- **ORM**: SQLAlchemy 2.0 async（aiosqlite 驱动）
- **存储**: SQLite（结构化数据）+ 文件系统（Agent 记忆等 blob）
- **数据模型**: Pydantic v2
- **CLI**: rich（输出美化）+ prompt_toolkit（异步交互输入）
- **日志**: structlog（结构化日志，contextvars 绑定 Agent 上下文）
- **配置**: pydantic-settings（TYRANT_* 环境变量 + ~/.config/tyrant_core/.env）
- **LLM 适配**: litellm（非 Gemini 模型通过 OpenAI 兼容 API 调用）
- **包管理**: uv
- **测试**: pytest + pytest-asyncio

## 项目结构

```
src/
  tyrant_core/
    __init__.py
    __main__.py             # 启动入口
    config.py               # pydantic-settings 配置
    models.py               # 数据模型（Node, Relation, Message, Task, Event, Reference）
    store/
      __init__.py
      base.py               # Store 抽象接口
      sqlite_store.py        # SQLite 实现
    bus/
      __init__.py
      message_bus.py         # 消息总线（路由、投递、ACK）
      event_bus.py           # 事件总线（pub/sub、日志）
    api/
      __init__.py
      system_api.py          # 系统 API（所有对外操作的入口）
    runtime/
      __init__.py
      agent_runtime.py       # Agent 运行时（ADK 集成、生命周期管理）
    adapters/
      __init__.py
      cli.py                # CLI 适配器（Tyrant 终端交互）
tests/
    conftest.py
    test_models.py
    test_store.py
    test_event_bus.py
    test_message_bus.py
    test_system_api.py
```

## 模块依赖图

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

依赖方向：上层依赖下层，不反向。

- **models** — 零依赖，纯数据定义
- **store** — 依赖 models
- **message_bus** — 依赖 store、models
- **event_bus** — 依赖 store、models
- **system_api** — 依赖 store、message_bus、event_bus、models
- **agent_runtime** — 依赖 system_api、message_bus、event_bus、adk
- **cli_adapter** — 依赖 system_api、message_bus

## 启动流程

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

async def main():
    settings = Settings()  # 从 ~/.config/tyrant_core/.env + 环境变量加载
    settings.apply_env()   # 注入 OPENAI_API_KEY/BASE 供 LiteLLM 使用

    engine = create_async_engine(f"sqlite+aiosqlite:///{settings.data_dir}/tyrant.db")
    store = SqliteStore(engine)
    await store.initialize()  # 创建表

    event_bus = EventBus(store)
    message_bus = MessageBus(store, event_bus)
    api = SystemAPI(store, message_bus, event_bus)
    runtime = AgentRuntime(api, message_bus, event_bus, settings)
    cli = CliAdapter(api, message_bus)

    # 从持久化恢复状态
    await runtime.recover()

    # 启动 CLI（Tyrant 交互入口）
    try:
        await cli.run()
    finally:
        await runtime.shutdown()
        await store.close()
```

## 约定

- 所有 ID 使用 uuid4 字符串
- 时间统一用 UTC datetime
- 异步方法统一用 async/await
- 错误通过自定义异常抛出，不用错误码
- 日志用 structlog，通过 contextvars 自动绑定 node_id 等上下文
- 配置通过 pydantic-settings，支持 TYRANT_* 环境变量和 `~/.config/tyrant_core/.env`

## 依赖清单

```
# 核心
google-adk           # Agent 框架（附带 pydantic, aiosqlite, sqlalchemy 等）
pydantic>=2.0
pydantic-settings    # 配置管理
litellm              # 非 Gemini 模型适配

# ORM / 存储
sqlalchemy[asyncio]>=2.0  # 含 greenlet
aiosqlite            # SQLAlchemy async SQLite 驱动

# CLI
rich                 # 终端美化输出
prompt-toolkit       # 异步交互输入

# 日志
structlog            # 结构化日志

# 测试
pytest
pytest-asyncio
```
