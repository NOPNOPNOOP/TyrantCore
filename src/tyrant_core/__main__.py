"""TyrantCore 启动入口。组装 Store → Bus → API → Runtime → CLI。"""

from __future__ import annotations

import asyncio
import os

import structlog
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.config import get_settings
from tyrant_core.store.sqlite_store import SqliteStore
from tyrant_core.bus.event_bus import EventBus
from tyrant_core.bus.message_bus import MessageBus
from tyrant_core.api.system_api import SystemAPI
from tyrant_core.runtime.agent_runtime import AgentRuntime
from tyrant_core.adapters.cli import CliAdapter


async def main() -> None:
    # 0. 加载配置
    settings = get_settings()
    settings.apply_env()  # 注入 OPENAI_API_KEY/BASE 供 LiteLLM 使用

    # structlog 配置
    log_level = getattr(__import__("logging"), settings.log_level.upper(), 20)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
    )
    logger = structlog.get_logger()

    logger.info(
        "config_loaded",
        model=settings.get_litellm_model(),
        api_base=settings.api_base or "(default)",
        data_dir=settings.data_dir,
    )

    # 确保数据目录存在
    data_dir = settings.data_dir
    db_path = os.path.join(data_dir, "tyrant.db")
    os.makedirs(data_dir, exist_ok=True)

    # 1. Store
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)
    store = SqliteStore(engine)
    await store.initialize()
    logger.info("store_initialized", db_path=db_path)

    try:
        # 2. Bus
        event_bus = EventBus(store)
        message_bus = MessageBus(store, event_bus)

        # 3. API
        api = SystemAPI(store, message_bus, event_bus)

        # 4. Runtime
        runtime = AgentRuntime(api, message_bus, event_bus, settings=settings)
        await runtime.recover()

        # 5. CLI
        cli = CliAdapter(api, message_bus, settings.tyrant_node_id, runtime=runtime)

        logger.info("tyrant_core_started")
        await cli.run()

    except KeyboardInterrupt:
        logger.info("shutting_down")
    finally:
        if "runtime" in dir():
            await runtime.shutdown()
        await store.close()
        logger.info("tyrant_core_stopped")


if __name__ == "__main__":
    asyncio.run(main())
