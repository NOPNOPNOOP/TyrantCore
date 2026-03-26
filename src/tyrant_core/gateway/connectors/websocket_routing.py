"""WebSocket egress 路由策略。"""

from __future__ import annotations

from typing import Literal

from tyrant_core.models import ValidationError

from .websocket_connections import WebSocketConnection, WebSocketConnectionRegistry

WebSocketRoutingMode = Literal[
    "PREFER_REQUESTED",
    "LAST_ACTIVE",
    "FIRST_ACTIVE",
    "BROADCAST",
]

_VALID_ROUTING_MODES = {
    "PREFER_REQUESTED",
    "LAST_ACTIVE",
    "FIRST_ACTIVE",
    "BROADCAST",
}


class WebSocketDeliveryRouter:
    """决定 actor 的 delivery 应路由到哪些活跃连接。"""

    def __init__(
        self,
        registry: WebSocketConnectionRegistry,
        *,
        mode: WebSocketRoutingMode = "PREFER_REQUESTED",
    ) -> None:
        if mode not in _VALID_ROUTING_MODES:
            raise ValidationError(f"unsupported websocket routing mode: {mode}")
        self.registry = registry
        self.mode: WebSocketRoutingMode = mode

    def route(
        self,
        *,
        actor_id: str,
        preferred_connection_id: str | None = None,
    ) -> list[WebSocketConnection]:
        active = self.registry.list_for_actor(actor_id)
        if not active:
            return []

        if self.mode == "BROADCAST":
            return active

        if self.mode == "FIRST_ACTIVE":
            return [active[0]]

        by_activity = sorted(
            active,
            key=lambda item: (item.last_seen_at, item.connected_at, item.connection_id),
        )

        if self.mode == "LAST_ACTIVE":
            return [by_activity[-1]]

        if preferred_connection_id is not None:
            preferred = self.registry.get(preferred_connection_id)
            if preferred is not None and preferred.actor_id == actor_id:
                return [preferred]

        return [by_activity[-1]]
