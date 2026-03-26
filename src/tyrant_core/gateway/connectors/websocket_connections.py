"""WebSocket 连接注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tyrant_core.models import ValidationError


@dataclass(slots=True)
class WebSocketConnection:
    """进程内维护的 websocket 活跃连接状态。"""

    connection_id: str
    actor_id: str
    attachment_id: str
    external_key: str
    account_id: str | None
    thread_id: str | None
    metadata: dict[str, Any]
    capabilities: list[str]
    connected_at: datetime
    last_seen_at: datetime


class WebSocketConnectionRegistry:
    """负责维护活跃 websocket 连接。"""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocketConnection] = {}
        self._actor_index: dict[str, set[str]] = {}

    def add(self, connection: WebSocketConnection) -> None:
        if connection.connection_id in self._connections:
            raise ValidationError(
                f"websocket connection already exists: {connection.connection_id}"
            )
        self._connections[connection.connection_id] = connection
        self._actor_index.setdefault(connection.actor_id, set()).add(
            connection.connection_id
        )

    def remove(self, connection_id: str) -> WebSocketConnection | None:
        connection = self._connections.pop(connection_id, None)
        if connection is None:
            return None
        actor_connections = self._actor_index.get(connection.actor_id)
        if actor_connections is not None:
            actor_connections.discard(connection_id)
            if not actor_connections:
                self._actor_index.pop(connection.actor_id, None)
        return connection

    def get(self, connection_id: str) -> WebSocketConnection | None:
        return self._connections.get(connection_id)

    def touch(self, connection_id: str) -> WebSocketConnection | None:
        connection = self._connections.get(connection_id)
        if connection is None:
            return None
        connection.last_seen_at = datetime.now(timezone.utc)
        return connection

    def list_connection_ids(self) -> list[str]:
        return list(self._connections)

    def list_for_actor(self, actor_id: str) -> list[WebSocketConnection]:
        connection_ids = self._actor_index.get(actor_id, set())
        connections = [
            self._connections[connection_id]
            for connection_id in connection_ids
            if connection_id in self._connections
        ]
        return sorted(
            connections,
            key=lambda item: (item.connected_at, item.connection_id),
        )

    def snapshot(self, connection_id: str) -> dict[str, Any] | None:
        connection = self._connections.get(connection_id)
        if connection is None:
            return None
        return {
            "connection_id": connection.connection_id,
            "actor_id": connection.actor_id,
            "attachment_id": connection.attachment_id,
            "external_key": connection.external_key,
            "account_id": connection.account_id,
            "thread_id": connection.thread_id,
            "metadata": dict(connection.metadata),
            "capabilities": list(connection.capabilities),
        }
