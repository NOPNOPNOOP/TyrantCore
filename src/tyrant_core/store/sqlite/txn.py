"""SQLite Store 的事务与会话管理。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator, Callable

from sqlalchemy import Table, event
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tyrant_core.config import get_settings

from .tables import metadata


def build_default_database_url(
    *,
    get_settings_fn: Callable[[], object] = get_settings,
) -> str:
    """根据当前配置解析默认数据库路径。"""
    settings = get_settings_fn()
    data_dir = Path(settings.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = (data_dir / "tyrant_core_v2.db").resolve()
    return f"sqlite+aiosqlite:///{db_path}"


class SqliteSessionScope:
    """负责 engine、session 与事务上下文。"""

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        database_url: str | None = None,
        default_database_url: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine or create_async_engine(
            database_url or (default_database_url or build_default_database_url)()
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self._current_session_var: ContextVar[AsyncSession | None] = ContextVar(
            "tyrant_core_v2_current_session",
            default=None,
        )
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

        @event.listens_for(self._engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def session(self) -> AsyncSession:
        """返回当前事务中的 session。"""
        session = self._current_session_var.get()
        if session is None:
            raise RuntimeError("必须在 store.transaction() 上下文中执行操作")
        return session

    async def upsert(
        self,
        table: Table,
        values: dict,
        *,
        conflict_columns: tuple[str, ...] = ("id",),
    ) -> None:
        """执行 SQLite upsert。"""
        session = self.session()
        stmt = sqlite_insert(table).values(**values)
        update_values = {
            key: stmt.excluded[key]
            for key in values
            if key not in conflict_columns
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c[column] for column in conflict_columns],
            set_=update_values,
        )
        await session.execute(stmt)

    async def initialize(self) -> None:
        """懒初始化数据库表。"""
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            async with self._engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            self._initialized = True

    async def close(self) -> None:
        """关闭 engine。"""
        await self._engine.dispose()
        self._initialized = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """提供支持嵌套的事务上下文。"""
        await self.initialize()
        existing = self._current_session_var.get()
        if existing is not None:
            yield
            return
        async with self._session_factory() as session:
            async with session.begin():
                token = self._current_session_var.set(session)
                try:
                    yield
                finally:
                    self._current_session_var.reset(token)

