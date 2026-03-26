"""v2 查询投影。"""

from __future__ import annotations

from dataclasses import dataclass

from tyrant_core.store import Store


@dataclass(slots=True)
class DeliveryProjection:
    """Delivery 的只读投影。"""

    store: Store

    async def pending_for_actor(self, actor_id: str, limit: int = 100) -> list[dict]:
        async with self.store.transaction():
            deliveries = await self.store.query_deliveries(
                recipient_actor_id=actor_id,
                state="PENDING",
                limit=limit,
            )
        return [delivery.model_dump(mode="json") for delivery in deliveries]

    async def acked_for_actor(self, actor_id: str, limit: int = 100) -> list[dict]:
        async with self.store.transaction():
            deliveries = await self.store.query_deliveries(
                recipient_actor_id=actor_id,
                state="ACKED",
                limit=limit,
            )
        return [delivery.model_dump(mode="json") for delivery in deliveries]


@dataclass(slots=True)
class SessionProjection:
    """Session 的只读投影。"""

    store: Store

    async def active(self, *, surface: str | None = None, limit: int = 100) -> list[dict]:
        async with self.store.transaction():
            sessions = await self.store.query_sessions(
                surface=surface,
                state="OPEN",
                limit=limit,
            )
        return [session.model_dump(mode="json") for session in sessions]


__all__ = ["DeliveryProjection", "SessionProjection"]
