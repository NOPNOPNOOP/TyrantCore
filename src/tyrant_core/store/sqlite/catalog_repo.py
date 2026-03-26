"""组织目录域仓储。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select, update

from tyrant_core.models import Actor, Grant, OrgEdge

from .codec import dt_to_str, model_to_row, normalize_value, row_to_model
from .support import SqliteRepositorySupport
from .tables import actors_table, grants_table, org_edges_table


class CatalogRepository(SqliteRepositorySupport):
    """负责 Actor / OrgEdge / Grant。"""

    async def save_actor(self, actor: Actor) -> None:
        await self.upsert(actors_table, model_to_row(actor))

    async def get_actor(self, actor_id: str) -> Actor | None:
        result = await self.session().execute(
            select(actors_table).where(actors_table.c.id == actor_id)
        )
        return row_to_model(Actor, result.mappings().first())

    async def update_actor(self, actor_id: str, **fields) -> None:
        values = {key: normalize_value(value) for key, value in fields.items()}
        if not values:
            return
        await self.session().execute(
            update(actors_table).where(actors_table.c.id == actor_id).values(**values)
        )

    async def list_actors(self, **filters) -> list[Actor]:
        stmt = select(actors_table)
        tags = filters.pop("tags", None)
        for key, value in filters.items():
            column = getattr(actors_table.c, key, None)
            if column is None:
                continue
            stmt = stmt.where(column == normalize_value(value))
        stmt = stmt.order_by(actors_table.c.created_at.desc())
        result = await self.session().execute(stmt)
        actors = [row_to_model(Actor, row) for row in result.mappings().all()]
        if tags is None:
            return actors
        filter_tags = set(tags if isinstance(tags, list) else [tags])
        return [actor for actor in actors if filter_tags & set(actor.tags)]

    async def save_org_edge(self, edge: OrgEdge) -> None:
        await self.upsert(org_edges_table, model_to_row(edge))

    async def list_org_edges(self, **filters) -> list[OrgEdge]:
        stmt = select(org_edges_table)
        for key, value in filters.items():
            column = getattr(org_edges_table.c, key, None)
            if column is None:
                continue
            stmt = stmt.where(column == normalize_value(value))
        stmt = stmt.order_by(org_edges_table.c.created_at.desc())
        result = await self.session().execute(stmt)
        return [row_to_model(OrgEdge, row) for row in result.mappings().all()]

    async def save_grant(self, grant: Grant) -> None:
        await self.upsert(grants_table, model_to_row(grant))

    async def list_grants(self, subject_actor_id: str) -> list[Grant]:
        now = dt_to_str(datetime.now(timezone.utc))
        stmt = (
            select(grants_table)
            .where(grants_table.c.subject_actor_id == subject_actor_id)
            .where(grants_table.c.revoked_at.is_(None))
            .where(
                or_(
                    grants_table.c.expires_at.is_(None),
                    grants_table.c.expires_at > now,
                )
            )
            .order_by(grants_table.c.created_at.desc())
        )
        result = await self.session().execute(stmt)
        return [row_to_model(Grant, row) for row in result.mappings().all()]

