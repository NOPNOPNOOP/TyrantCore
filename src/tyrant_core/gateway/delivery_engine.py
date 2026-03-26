"""Gateway 投递引擎。"""

from __future__ import annotations

from dataclasses import dataclass

from tyrant_core.models import Delivery
from tyrant_core.store import Store


@dataclass(slots=True)
class DeliveryLease:
    """投递租约。"""

    delivery: Delivery
    consumer_id: str


class DeliveryEngine:
    """基于 Store 的 delivery 状态机。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def lease(
        self,
        *,
        actor_id: str,
        consumer_id: str,
        limit: int = 1,
        lease_seconds: int = 30,
    ) -> list[DeliveryLease]:
        async with self.store.transaction():
            deliveries = await self.store.lease_deliveries(
                recipient_actor_id=actor_id,
                lease_owner=consumer_id,
                limit=limit,
                lease_seconds=lease_seconds,
            )
        return [
            DeliveryLease(delivery=delivery, consumer_id=consumer_id)
            for delivery in deliveries
        ]

    async def ack(self, delivery_id: str) -> None:
        async with self.store.transaction():
            await self.store.ack_delivery(delivery_id)

    async def fail(self, delivery_id: str, error: dict | None = None) -> None:
        async with self.store.transaction():
            await self.store.fail_delivery(delivery_id, error=error)

    async def query(
        self,
        *,
        actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Delivery]:
        async with self.store.transaction():
            return await self.store.query_deliveries(
                recipient_actor_id=actor_id,
                state=state,
                limit=limit,
            )
