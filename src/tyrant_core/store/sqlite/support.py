"""SQLite Store 仓储共用底座。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession

from .txn import SqliteSessionScope


@dataclass(slots=True)
class SqliteRepositorySupport:
    """为各领域仓储提供统一的 session / upsert 入口。"""

    scope: SqliteSessionScope

    def session(self) -> AsyncSession:
        return self.scope.session()

    async def upsert(
        self,
        table: Table,
        values: dict,
        *,
        conflict_columns: tuple[str, ...] = ("id",),
    ) -> None:
        await self.scope.upsert(
            table,
            values,
            conflict_columns=conflict_columns,
        )

