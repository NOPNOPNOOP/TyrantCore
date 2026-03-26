"""直接绑定真实 websocket 连接的传输实现。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from tyrant_core.models import ValidationError

from .websocket_transport import WebSocketTransport


class JsonTextWebSocket(Protocol):
    """最小 websocket 文本发送协议。"""

    async def recv(self) -> str | bytes: ...

    async def send(self, message: str) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass(slots=True)
class _SocketBinding:
    socket: JsonTextWebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    opened: bool = False
    connection: dict[str, Any] | None = None


class DirectWebSocketTransport(WebSocketTransport):
    """把 transport.send 直接落到真实 websocket socket。"""

    def __init__(self) -> None:
        self._bindings: dict[str, _SocketBinding] = {}

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        for connection_id in list(self._bindings):
            await self.close(connection_id)

    async def bind_socket(
        self,
        *,
        connection_id: str,
        socket: JsonTextWebSocket,
    ) -> None:
        if connection_id in self._bindings:
            raise ValidationError(
                f"websocket socket is already bound: {connection_id}"
            )
        self._bindings[connection_id] = _SocketBinding(socket=socket)

    async def unbind_socket(self, connection_id: str) -> None:
        self._bindings.pop(connection_id, None)

    async def open(self, *, connection_id: str, connection: dict[str, Any]) -> None:
        binding = self._bindings.get(connection_id)
        if binding is None:
            raise ValidationError(f"websocket socket is not bound: {connection_id}")
        binding.connection = dict(connection)
        binding.opened = True

    async def close(self, connection_id: str) -> None:
        binding = self._bindings.pop(connection_id, None)
        if binding is None:
            return
        try:
            await binding.socket.close()
        finally:
            binding.opened = False
            binding.connection = None

    async def send(self, *, connection_id: str, message: dict[str, Any]) -> None:
        binding = self._bindings.get(connection_id)
        if binding is None or not binding.opened:
            raise ValidationError(
                f"websocket transport connection is not active: {connection_id}"
            )
        payload = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
        async with binding.send_lock:
            await binding.socket.send(payload)

    async def receive_once(
        self,
        *,
        connection_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        del limit
        binding = self._bindings.get(connection_id)
        if binding is None or not binding.opened:
            raise ValidationError(
                f"websocket transport connection is not active: {connection_id}"
            )
        return []
