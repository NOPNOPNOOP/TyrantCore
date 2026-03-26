"""管理域仓储。"""

from __future__ import annotations

from sqlalchemy import select, update

from tyrant_core.models import ActorControl, ActorProfile

from .codec import model_to_row, normalize_value, row_to_model
from .support import SqliteRepositorySupport
from .tables import actor_controls_table, actor_profiles_table


class ManagementRepository(SqliteRepositorySupport):
    """负责 ActorProfile / ActorControl。"""

    async def save_actor_profile(self, profile: ActorProfile) -> None:
        await self.upsert(actor_profiles_table, model_to_row(profile))

    async def get_actor_profile(self, profile_id: str) -> ActorProfile | None:
        result = await self.session().execute(
            select(actor_profiles_table).where(actor_profiles_table.c.id == profile_id)
        )
        return row_to_model(ActorProfile, result.mappings().first())

    async def query_actor_profiles(
        self,
        *,
        archived: bool | None = None,
        limit: int = 50,
    ) -> list[ActorProfile]:
        stmt = select(actor_profiles_table)
        if archived is True:
            stmt = stmt.where(actor_profiles_table.c.archived_at.is_not(None))
        elif archived is False:
            stmt = stmt.where(actor_profiles_table.c.archived_at.is_(None))
        stmt = stmt.order_by(actor_profiles_table.c.created_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(ActorProfile, row) for row in result.mappings().all()]

    async def save_actor_control(self, control: ActorControl) -> None:
        await self.upsert(
            actor_controls_table,
            model_to_row(control),
            conflict_columns=("actor_id",),
        )

    async def get_actor_control(self, actor_id: str) -> ActorControl | None:
        result = await self.session().execute(
            select(actor_controls_table).where(actor_controls_table.c.actor_id == actor_id)
        )
        return row_to_model(ActorControl, result.mappings().first())

    async def update_actor_control(self, actor_id: str, **fields) -> None:
        values = {key: normalize_value(value) for key, value in fields.items()}
        if not values:
            return
        await self.session().execute(
            update(actor_controls_table)
            .where(actor_controls_table.c.actor_id == actor_id)
            .values(**values)
        )

    async def query_actor_controls(
        self,
        *,
        manager_actor_id: str | None = None,
        desired_state: str | None = None,
        runtime_kind: str | None = None,
        limit: int = 50,
    ) -> list[ActorControl]:
        stmt = select(actor_controls_table)
        if manager_actor_id is not None:
            stmt = stmt.where(actor_controls_table.c.manager_actor_id == manager_actor_id)
        if desired_state is not None:
            stmt = stmt.where(actor_controls_table.c.desired_state == desired_state)
        if runtime_kind is not None:
            stmt = stmt.where(actor_controls_table.c.runtime_kind == runtime_kind)
        stmt = stmt.order_by(actor_controls_table.c.updated_at.desc()).limit(limit)
        result = await self.session().execute(stmt)
        return [row_to_model(ActorControl, row) for row in result.mappings().all()]
