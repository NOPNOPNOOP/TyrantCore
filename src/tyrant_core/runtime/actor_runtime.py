"""v2 Actor 运行时。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from tyrant_core.gateway import GatewayServer

HandlerResult = dict[str, Any] | None
DeliveryHandler = Callable[
    [dict[str, Any], dict[str, Any]],
    HandlerResult | Awaitable[HandlerResult],
]


class ActorRuntime:
    """通过 Gateway 领取投递并执行。"""

    def __init__(self, gateway: GatewayServer) -> None:
        self.gateway = gateway

    async def consume_once(
        self,
        *,
        actor_id: str,
        consumer_id: str,
        handler: DeliveryHandler,
        limit: int = 1,
        lease_seconds: int = 30,
    ) -> int:
        leased = await self.gateway.lease_deliveries(
            actor_id=actor_id,
            consumer_id=consumer_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        processed = 0
        for item in leased:
            delivery = item["delivery"]
            envelope = await self.gateway.get_envelope(delivery["envelope_id"])
            try:
                result = handler(envelope, delivery)
                if inspect.isawaitable(result):
                    result = await result
                if result:
                    await self.gateway.ingest({
                        "sender_actor_id": actor_id,
                        "recipient_actor_ids": result["recipient_actor_ids"],
                        "thread_id": result.get("thread_id", envelope["thread_id"]),
                        "kind": result.get("kind", "CHAT"),
                        "payload": result.get("payload", {}),
                        "priority": result.get("priority", envelope["priority"]),
                        "correlation_id": result.get("correlation_id", envelope["id"]),
                        "bypass_policy": True,
                    })
                await self.gateway.ack_delivery(delivery["id"])
                processed += 1
            except Exception as exc:
                await self.gateway.fail_delivery(
                    delivery["id"],
                    error={"error": str(exc), "consumer_id": consumer_id},
                )
                raise
        return processed
