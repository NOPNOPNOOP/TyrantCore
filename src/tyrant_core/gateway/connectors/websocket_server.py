"""WebSocket 网络 server 壳层。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

from websockets.asyncio.server import serve as websockets_serve
from websockets.exceptions import ConnectionClosed

from tyrant_core.models import ValidationError

from .websocket import WebSocketConnector
from .websocket_socket_transport import DirectWebSocketTransport, JsonTextWebSocket


class RunningWebSocketServer(Protocol):
    """运行中的 websocket server 最小协议。"""

    sockets: list[Any] | None

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


class WebSocketServeFn(Protocol):
    """创建 websocket server 的工厂协议。"""

    async def __call__(
        self,
        handler: Callable[[Any], Awaitable[None]],
        host: str,
        port: int,
        **kwargs: Any,
    ) -> RunningWebSocketServer: ...


@dataclass(slots=True)
class WebSocketSessionContext:
    """网络连接与 actor/session 绑定后的上下文。"""

    connection_id: str
    actor_id: str
    external_key: str
    account_id: str | None
    thread_id: str | None


class WebSocketServerShell:
    """把真实 websocket socket 接到 WebSocketConnector。"""

    def __init__(
        self,
        connector: WebSocketConnector,
        *,
        transport: DirectWebSocketTransport | None = None,
        host: str = "127.0.0.1",
        port: int = 8765,
        poll_interval: float = 0.05,
        egress_limit: int = 20,
        lease_seconds: int = 30,
        serve_fn: WebSocketServeFn | None = None,
        serve_kwargs: dict[str, Any] | None = None,
        name: str = "websocket-server",
    ) -> None:
        if transport is None:
            if not isinstance(connector.transport, DirectWebSocketTransport):
                raise ValidationError(
                    "WebSocketServerShell requires DirectWebSocketTransport"
                )
            transport = connector.transport
        elif connector.transport is not transport:
            raise ValidationError(
                "connector.transport must match WebSocketServerShell transport"
            )

        self.connector = connector
        self.transport = transport
        self.host = host
        self.port = port
        self.poll_interval = poll_interval
        self.egress_limit = egress_limit
        self.lease_seconds = lease_seconds
        self.serve_fn = serve_fn or websockets_serve
        self.serve_kwargs = dict(serve_kwargs or {})
        self.name = name
        self.bound_host: str | None = None
        self.bound_port: int | None = None
        self._server: RunningWebSocketServer | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        await self.connector.start()
        try:
            self._server = await self.serve_fn(
                self._handle_socket,
                self.host,
                self.port,
                **self.serve_kwargs,
            )
        except Exception:
            await self.connector.stop()
            raise
        self._capture_bound_address()

    async def stop(self) -> None:
        server = self._server
        self._server = None
        try:
            if server is not None:
                server.close()
                await server.wait_closed()
        finally:
            self.bound_host = None
            self.bound_port = None
            await self.connector.stop()

    def url(self) -> str | None:
        if self.bound_host is None or self.bound_port is None:
            return None
        return f"ws://{self.bound_host}:{self.bound_port}"

    async def _handle_socket(self, socket: JsonTextWebSocket) -> None:
        context: WebSocketSessionContext | None = None
        egress_task: asyncio.Task[None] | None = None
        try:
            hello = await self._recv_json(socket)
            context = await self._open_connection(socket, hello)
            await self.transport.send(
                connection_id=context.connection_id,
                message={
                    "type": "connected",
                    "connection": {
                        "connection_id": context.connection_id,
                        "actor_id": context.actor_id,
                        "external_key": context.external_key,
                        "thread_id": context.thread_id,
                    },
                },
            )
            egress_task = asyncio.create_task(
                self._egress_loop(context.connection_id),
                name=f"websocket-egress:{context.connection_id}",
            )

            while True:
                message = await self._recv_json(socket)
                response, should_close = await self._dispatch_message(context, message)
                if response is not None:
                    await self.transport.send(
                        connection_id=context.connection_id,
                        message=response,
                    )
                if should_close:
                    break
        except ConnectionClosed:
            return
        except Exception as exc:
            await self._safe_send_error(socket, context, exc)
        finally:
            if egress_task is not None:
                egress_task.cancel()
                try:
                    await egress_task
                except asyncio.CancelledError:
                    pass
            if context is not None:
                await self.connector.disconnect(context.connection_id)
            await self._safe_close(socket)

    async def _open_connection(
        self,
        socket: JsonTextWebSocket,
        hello: dict[str, Any],
    ) -> WebSocketSessionContext:
        if hello.get("type") != "connect":
            raise ValidationError("first websocket message must be type=connect")
        external_key = hello.get("external_key")
        if not isinstance(external_key, str) or not external_key:
            raise ValidationError("connect.external_key is required")
        requested_connection_id = hello.get("connection_id")
        if requested_connection_id is None:
            requested_connection_id = str(uuid4())
        if not isinstance(requested_connection_id, str) or not requested_connection_id:
            raise ValidationError("connect.connection_id must be a non-empty string")

        await self.transport.bind_socket(
            connection_id=requested_connection_id,
            socket=socket,
        )
        try:
            result = await self.connector.connect(
                external_key=external_key,
                actor_id=hello.get("actor_id"),
                account_id=hello.get("account_id"),
                connection_id=requested_connection_id,
                thread_id=hello.get("thread_id"),
                metadata=self._maybe_dict(hello.get("metadata"), "connect.metadata"),
                capabilities=self._maybe_str_list(
                    hello.get("capabilities"),
                    "connect.capabilities",
                ),
            )
        except Exception:
            await self.transport.unbind_socket(requested_connection_id)
            raise

        connection = await self.connector.get_connection(result["connection_id"])
        if connection is None:
            raise ValidationError("websocket connection state is missing after connect")
        return WebSocketSessionContext(
            connection_id=result["connection_id"],
            actor_id=result["actor_id"],
            external_key=result["external_key"],
            account_id=connection.get("account_id"),
            thread_id=result.get("thread_id"),
        )

    async def _dispatch_message(
        self,
        context: WebSocketSessionContext,
        message: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        action = message.get("type")
        if action == "ping":
            return {"type": "pong"}, False

        if action == "disconnect":
            return {
                "type": "disconnecting",
                "connection_id": context.connection_id,
            }, True

        if action == "send_text":
            text = message.get("text")
            if not isinstance(text, str):
                raise ValidationError("send_text.text is required")
            result = await self.connector.send_text(
                external_key=context.external_key,
                recipient_actor_id=message.get("recipient_actor_id"),
                recipient_actor_ids=message.get("recipient_actor_ids"),
                text=text,
                sender_actor_id=context.actor_id,
                account_id=context.account_id,
                thread_id=message.get("thread_id", context.thread_id),
                kind=message.get("kind"),
                subject=message.get("subject"),
                metadata=self._maybe_dict(message.get("metadata"), "send_text.metadata"),
                bypass_policy=bool(message.get("bypass_policy", True)),
            )
            context.thread_id = result.get("thread_id", context.thread_id)
            return {
                "type": "result",
                "action": "send_text",
                "result": result,
            }, False

        if action == "send_payload":
            payload = self._maybe_dict(message.get("payload"), "send_payload.payload")
            result = await self.connector.send_payload(
                external_key=context.external_key,
                recipient_actor_id=message.get("recipient_actor_id"),
                recipient_actor_ids=message.get("recipient_actor_ids"),
                payload=payload,
                sender_actor_id=context.actor_id,
                account_id=context.account_id,
                thread_id=message.get("thread_id", context.thread_id),
                kind=message.get("kind"),
                subject=message.get("subject"),
                metadata=self._maybe_dict(
                    message.get("metadata"),
                    "send_payload.metadata",
                ),
                bypass_policy=bool(message.get("bypass_policy", True)),
            )
            context.thread_id = result.get("thread_id", context.thread_id)
            return {
                "type": "result",
                "action": "send_payload",
                "result": result,
            }, False

        raise ValidationError(f"unsupported websocket action: {action}")

    async def _egress_loop(self, connection_id: str) -> None:
        while True:
            try:
                delivered = await self.connector.pump_outbound_once(
                    connection_id=connection_id,
                    limit=self.egress_limit,
                    lease_seconds=self.lease_seconds,
                )
            except ValidationError:
                return
            if delivered:
                await asyncio.sleep(0)
                continue
            await asyncio.sleep(self.poll_interval)

    async def _recv_json(self, socket: JsonTextWebSocket) -> dict[str, Any]:
        raw = await socket.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not isinstance(raw, str):
            raise ValidationError("websocket message must be text")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError("websocket message must be valid json") from exc
        if not isinstance(payload, dict):
            raise ValidationError("websocket message must be a json object")
        return payload

    async def _safe_send_error(
        self,
        socket: JsonTextWebSocket,
        context: WebSocketSessionContext | None,
        exc: Exception,
    ) -> None:
        message = {
            "type": "error",
            "error": {
                "kind": exc.__class__.__name__,
                "message": str(exc),
            },
        }
        try:
            if context is None:
                await socket.send(json.dumps(message, ensure_ascii=True))
            else:
                await self.transport.send(
                    connection_id=context.connection_id,
                    message=message,
                )
        except Exception:
            return

    async def _safe_close(self, socket: JsonTextWebSocket) -> None:
        try:
            await socket.close()
        except Exception:
            return

    def _capture_bound_address(self) -> None:
        if self._server is None or not self._server.sockets:
            self.bound_host = self.host
            self.bound_port = self.port
            return
        host, port, *_ = self._server.sockets[0].getsockname()
        self.bound_host = str(host)
        self.bound_port = int(port)

    def _maybe_dict(self, value: Any, field_name: str) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValidationError(f"{field_name} must be a json object")
        return dict(value)

    def _maybe_str_list(
        self,
        value: Any,
        field_name: str,
    ) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValidationError(f"{field_name} must be a list[str]")
        return list(value)
