"""线程与信封域仓储。"""

from __future__ import annotations

from sqlalchemy import select

from tyrant_core.models import Envelope, Thread

from .codec import model_to_row, row_to_model
from .support import SqliteRepositorySupport
from .tables import envelopes_table, threads_table


class ThreadRepository(SqliteRepositorySupport):
    """负责 Thread / Envelope。"""

    async def save_thread(self, thread: Thread) -> None:
        await self.upsert(threads_table, model_to_row(thread))

    async def get_thread(self, thread_id: str) -> Thread | None:
        result = await self.session().execute(
            select(threads_table).where(threads_table.c.id == thread_id)
        )
        return row_to_model(Thread, result.mappings().first())

    async def save_envelope(self, envelope: Envelope) -> None:
        await self.upsert(envelopes_table, model_to_row(envelope))

    async def get_envelope(self, envelope_id: str) -> Envelope | None:
        result = await self.session().execute(
            select(envelopes_table).where(envelopes_table.c.id == envelope_id)
        )
        return row_to_model(Envelope, result.mappings().first())

    async def query_envelopes(
        self,
        *,
        thread_id: str | None = None,
        sender_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[Envelope]:
        stmt = select(envelopes_table)
        if thread_id is not None:
            stmt = stmt.where(envelopes_table.c.thread_id == thread_id)
        if sender_actor_id is not None:
            stmt = stmt.where(envelopes_table.c.sender_actor_id == sender_actor_id)
        stmt = stmt.order_by(envelopes_table.c.created_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(Envelope, row) for row in result.mappings().all()]

