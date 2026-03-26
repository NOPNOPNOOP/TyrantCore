"""Gateway delivery 子控制器。"""

from __future__ import annotations

from typing import Any

from tyrant_core.api import SystemAPI
from tyrant_core.gateway.delivery_engine import DeliveryEngine


class GatewayDeliveryControl:
    """负责 delivery lease / ack / fail 与查询。"""

    def __init__(self, *, api: SystemAPI, deliveries: DeliveryEngine) -> None:
        self.api = api
        self.deliveries = deliveries

    async def lease_deliveries(
        self,
        *,
        actor_id: str,
        consumer_id: str,
        limit: int = 1,
        lease_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        leases = await self.deliveries.lease(
            actor_id=actor_id,
            consumer_id=consumer_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        return [
            {
                "consumer_id": lease.consumer_id,
                "delivery": lease.delivery.model_dump(mode="json"),
            }
            for lease in leases
        ]

    async def ack_delivery(self, delivery_id: str) -> None:
        await self.deliveries.ack(delivery_id)

    async def fail_delivery(self, delivery_id: str, error: dict | None = None) -> None:
        await self.deliveries.fail(delivery_id, error=error)

    async def list_pending_deliveries(self, actor_id: str) -> list[dict[str, Any]]:
        return await self.api.list_pending_deliveries(actor_id)

    async def query_deliveries(
        self,
        *,
        actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.api.query_deliveries(
            actor_id=actor_id,
            state=state,
            limit=limit,
        )
