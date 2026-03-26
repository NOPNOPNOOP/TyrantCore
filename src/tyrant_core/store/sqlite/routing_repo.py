"""路由与附着域仓储。"""

from __future__ import annotations

from sqlalchemy import select

from tyrant_core.models import Attachment, Binding, Presence, Session

from .codec import model_to_row, row_to_model
from .support import SqliteRepositorySupport
from .tables import attachments_table, bindings_table, presences_table, sessions_table


class RoutingRepository(SqliteRepositorySupport):
    """负责 Binding / Session / Attachment / Presence。"""

    async def save_binding(self, binding: Binding) -> None:
        await self.upsert(bindings_table, model_to_row(binding))

    async def query_bindings(
        self,
        *,
        surface: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[Binding]:
        stmt = select(bindings_table)
        if surface is not None:
            stmt = stmt.where(bindings_table.c.surface == surface)
        if actor_id is not None:
            stmt = stmt.where(bindings_table.c.actor_id == actor_id)
        stmt = stmt.order_by(
            bindings_table.c.priority.desc(),
            bindings_table.c.created_at.desc(),
        ).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(Binding, row) for row in result.mappings().all()]

    async def resolve_binding(
        self,
        *,
        surface: str,
        account_id: str | None = None,
        peer: str | None = None,
    ) -> Binding | None:
        stmt = (
            select(bindings_table)
            .where(bindings_table.c.surface == surface)
            .where(bindings_table.c.disabled_at.is_(None))
            .order_by(bindings_table.c.priority.desc(), bindings_table.c.created_at.desc())
        )
        result = await self.session().execute(stmt)
        candidates = [row_to_model(Binding, row) for row in result.mappings().all()]

        ranked: list[tuple[int, int, Binding]] = []
        for binding in candidates:
            specificity = 0
            if account_id is None:
                if binding.account_id is not None:
                    continue
            elif binding.account_id is not None:
                if binding.account_id != account_id:
                    continue
                specificity += 1

            if peer is None:
                if binding.peer is not None:
                    continue
            elif binding.peer is not None:
                if binding.peer != peer:
                    continue
                specificity += 1

            ranked.append((specificity, binding.priority, binding))

        if not ranked:
            return None
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return ranked[0][2]

    async def save_session(self, session_model: Session) -> None:
        await self.upsert(sessions_table, model_to_row(session_model))

    async def get_session(self, session_id: str) -> Session | None:
        result = await self.session().execute(
            select(sessions_table).where(sessions_table.c.id == session_id)
        )
        return row_to_model(Session, result.mappings().first())

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Session]:
        stmt = select(sessions_table)
        if actor_id is not None:
            stmt = stmt.where(sessions_table.c.actor_id == actor_id)
        if surface is not None:
            stmt = stmt.where(sessions_table.c.surface == surface)
        if external_key is not None:
            stmt = stmt.where(sessions_table.c.external_key == external_key)
        if state is not None:
            stmt = stmt.where(sessions_table.c.state == state)
        stmt = stmt.order_by(sessions_table.c.last_seen_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(Session, row) for row in result.mappings().all()]

    async def save_attachment(self, attachment: Attachment) -> None:
        await self.upsert(attachments_table, model_to_row(attachment))

    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Attachment]:
        stmt = select(attachments_table)
        if actor_id is not None:
            stmt = stmt.where(attachments_table.c.actor_id == actor_id)
        if kind is not None:
            stmt = stmt.where(attachments_table.c.kind == kind)
        stmt = stmt.order_by(attachments_table.c.created_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(Attachment, row) for row in result.mappings().all()]

    async def save_presence(self, presence: Presence) -> None:
        await self.upsert(
            presences_table,
            model_to_row(presence),
            conflict_columns=("attachment_id",),
        )

    async def get_presence(self, attachment_id: str) -> Presence | None:
        result = await self.session().execute(
            select(presences_table).where(presences_table.c.attachment_id == attachment_id)
        )
        return row_to_model(Presence, result.mappings().first())

