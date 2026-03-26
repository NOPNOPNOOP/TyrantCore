"""WebSocket 传输壳层。"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Protocol

from tyrant_core.models import ValidationError


class WebSocketTransport(Protocol):
    """面向真实网络实现的最小传输协议。"""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def open(self, *, connection_id: str, connection: dict[str, Any]) -> None: ...

    async def close(self, connection_id: str) -> None: ...

    async def send(self, *, connection_id: str, message: dict[str, Any]) -> None: ...

    async def receive_once(
        self,
        *,
        connection_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...


class InMemoryWebSocketTransport:
    """默认测试/嵌入式传输壳层。"""

    def __init__(self) -> None:
        self._connections: dict[str, dict[str, Any]] = {}
        self._outbound: dict[str, deque[dict[str, Any]]] = defaultdict(deque)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self._connections.clear()
        self._outbound.clear()

    async def open(self, *, connection_id: str, connection: dict[str, Any]) -> None:
        self._connections[connection_id] = dict(connection)
        self._outbound.setdefault(connection_id, deque())

    async def close(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)
        self._outbound.pop(connection_id, None)

    async def send(self, *, connection_id: str, message: dict[str, Any]) -> None:
        if connection_id not in self._connections:
            raise ValidationError(
                f"websocket transport connection is not active: {connection_id}"
            )
        self._outbound[connection_id].append(dict(message))

    async def receive_once(
        self,
        *,
        connection_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if connection_id not in self._connections:
            raise ValidationError(
                f"websocket transport connection is not active: {connection_id}"
            )
        queue = self._outbound[connection_id]
        messages: list[dict[str, Any]] = []
        while queue and len(messages) < limit:
            messages.append(queue.popleft())
        return messages
