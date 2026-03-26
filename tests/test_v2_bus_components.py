from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from tyrant_core.bus import EventBus, EventStream, SubscriptionHub
from tyrant_core.bus.message_bus import MessageBus
from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors import (
    DirectWebSocketTransport,
    InMemoryWebSocketTransport,
    WebSocketConnection,
    WebSocketConnectionRegistry,
    WebSocketConnector,
    WebSocketDeliveryRouter,
    WebSocketServerShell,
    WebhookConnector,
)
from tyrant_core.models import DomainEvent, UnsupportedLegacyComponentError, ValidationError
from tyrant_core.store import SqliteStore


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


class FailingOpenTransport:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def open(self, *, connection_id: str, connection: dict[str, Any]) -> None:
        raise RuntimeError("transport open failed")

    async def close(self, connection_id: str) -> None:
        return None

    async def send(self, *, connection_id: str, message: dict[str, Any]) -> None:
        return None

    async def receive_once(
        self,
        *,
        connection_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return []


class EmptyAfterLeaseRouter:
    def __init__(self, route: WebSocketConnection) -> None:
        self._route = route
        self._calls = 0

    def route(
        self,
        *,
        actor_id: str,
        preferred_connection_id: str | None = None,
    ) -> list[WebSocketConnection]:
        self._calls += 1
        if self._calls == 1:
            return [self._route]
        return []


class FakeWebSocket:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = 0
        for message in messages or []:
            self.feed_nowait(message)

    def feed_nowait(self, message: dict[str, Any]) -> None:
        self._incoming.put_nowait(json.dumps(message))

    async def feed(self, message: dict[str, Any]) -> None:
        await self._incoming.put(json.dumps(message))

    async def recv(self) -> str:
        return await self._incoming.get()

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.closed += 1


class FakeBoundSocket:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def getsockname(self) -> tuple[str, int]:
        return (self._host, self._port)


class FakeRunningWebSocketServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9876) -> None:
        self.sockets = [FakeBoundSocket(host, port)]
        self.closed = 0
        self.waited = 0

    def close(self) -> None:
        self.closed += 1

    async def wait_closed(self) -> None:
        self.waited += 1


class FakeServeFactory:
    def __init__(self, host: str = "127.0.0.1", port: int = 9876) -> None:
        self.server = FakeRunningWebSocketServer(host, port)
        self.calls: list[dict[str, Any]] = []
        self.handler = None

    async def __call__(self, handler, host: str, port: int, **kwargs):
        self.calls.append({
            "host": host,
            "port": port,
            "kwargs": dict(kwargs),
        })
        self.handler = handler
        return self.server


_DEFAULT_CONNECTION_STATE = object()


class FakeShellConnector:
    def __init__(
        self,
        transport: DirectWebSocketTransport,
        *,
        connect_result: dict[str, Any] | None = None,
        connection_state: Any = _DEFAULT_CONNECTION_STATE,
        connect_error: Exception | None = None,
        send_payload_result: dict[str, Any] | None = None,
        pump_error: Exception | None = None,
        disconnect_error: Exception | None = None,
    ) -> None:
        self.transport = transport
        self.connect_result = connect_result or {
            "connection_id": "conn-fake",
            "actor_id": "actor-fake",
            "external_key": "ws:user-fake",
            "thread_id": None,
        }
        if connection_state is _DEFAULT_CONNECTION_STATE:
            self.connection_state = {"account_id": "acct-fake"}
        elif connection_state is None:
            self.connection_state = None
        else:
            self.connection_state = dict(connection_state)
        self.connect_error = connect_error
        self.send_payload_result = send_payload_result or {
            "thread_id": "thread-fake",
            "envelope": {"id": "env-fake"},
        }
        self.pump_error = pump_error
        self.disconnect_error = disconnect_error
        self.started = 0
        self.stopped = 0
        self.connect_calls: list[dict[str, Any]] = []
        self.payload_calls: list[dict[str, Any]] = []
        self.disconnect_calls: list[str] = []

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def connect(self, **kwargs):
        self.connect_calls.append(dict(kwargs))
        if self.connect_error is not None:
            raise self.connect_error
        return dict(self.connect_result)

    async def get_connection(self, connection_id: str):
        del connection_id
        if self.connection_state is None:
            return None
        return dict(self.connection_state)

    async def send_text(self, **kwargs):
        return {
            "thread_id": kwargs.get("thread_id"),
            "envelope": {"id": "env-text"},
        }

    async def send_payload(self, **kwargs):
        self.payload_calls.append(dict(kwargs))
        return dict(self.send_payload_result)

    async def pump_outbound_once(self, **kwargs):
        if self.pump_error is not None:
            raise self.pump_error
        return []

    async def disconnect(self, connection_id: str) -> None:
        self.disconnect_calls.append(connection_id)
        if self.disconnect_error is not None:
            raise self.disconnect_error


class FailingCloseWebSocket(FakeWebSocket):
    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.closed += 1
        raise RuntimeError("close failed")


class FailingSendWebSocket(FakeWebSocket):
    async def send(self, message: str) -> None:
        del message
        raise RuntimeError("send failed")


class ClosedRecvWebSocket(FakeWebSocket):
    async def recv(self) -> str:
        raise ConnectionClosed(None, None)  # type: ignore[arg-type]


class FailingServeFactory:
    async def __call__(self, handler, host: str, port: int, **kwargs):
        del handler, host, port, kwargs
        raise RuntimeError("serve failed")


async def test_v2_event_bus_supports_typed_and_wildcard_subscribers() -> None:
    bus = EventBus()
    typed_queue = bus.subscribe("actor.created")
    wildcard_queue = bus.subscribe("*")
    event = DomainEvent(
        id="evt-1",
        type="actor.created",
        source="test",
        payload={"actor_id": "actor-1"},
        created_at=datetime.now(timezone.utc),
    )

    await bus.publish(event)

    assert await typed_queue.get() == event
    assert await wildcard_queue.get() == event
    assert bus.snapshot() == [event]


def test_v2_event_stream_keeps_append_only_snapshot() -> None:
    stream = EventStream(maxlen=2)

    stream.append({"id": "evt-1"})
    stream.append({"id": "evt-2"})
    stream.append({"id": "evt-3"})

    assert stream.snapshot() == [{"id": "evt-2"}, {"id": "evt-3"}]


async def test_v2_subscription_hub_broadcasts_to_topic_subscribers() -> None:
    hub = SubscriptionHub()
    queue = hub.subscribe("presence")

    await hub.publish("presence", {"status": "ONLINE"})

    assert await queue.get() == {"status": "ONLINE"}


def test_v2_message_bus_is_retired_sentinel() -> None:
    try:
        MessageBus()
    except UnsupportedLegacyComponentError as exc:
        assert "GatewayServer + DeliveryEngine" in str(exc)
    else:
        raise AssertionError("expected UnsupportedLegacyComponentError")


async def test_v2_webhook_connector_resolves_binding_and_ingests_event(tmp_path) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "webhook-connector.db"))
    )
    webhook = WebhookConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "hook-user",
            "kind": "EXTERNAL",
            "name": "Hook User",
            "slug": "hook_user",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })
        await gateway.register_binding({
            "id": "binding-hook-user",
            "surface": "webhook",
            "actor_id": "hook-user",
            "external_key": "hook:user-1",
            "account_id": "acct-1",
            "thread_policy": "REUSE",
        })

        assert webhook.name == "webhook"
        assert await webhook.start() is None
        result = await webhook.receive_event(
            external_key="hook:user-1",
            recipient_actor_id="worker",
            payload={"text": "hello from webhook"},
            account_id="acct-1",
            metadata={"source": "test"},
        )
        assert await webhook.stop() is None

        sessions = await gateway.query_sessions(
            surface="webhook",
            external_key="hook:user-1",
        )
        pending = await gateway.list_pending_deliveries("worker")

        assert result["envelope"]["kind"] == "WEBHOOK_EVENT"
        assert result["thread_id"] is not None
        assert len(sessions) == 1
        assert sessions[0]["actor_id"] == "hook-user"
        assert sessions[0]["metadata"]["account_id"] == "acct-1"
        assert sessions[0]["metadata"]["source"] == "test"
        assert len(pending) == 1
        assert pending[0]["envelope_id"] == result["envelope"]["id"]
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_reuses_thread_and_validates_binding(tmp_path) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-connector.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })
        await gateway.register_binding({
            "id": "binding-ws-user",
            "surface": "websocket",
            "actor_id": "ws-user",
            "external_key": "ws:user-1",
            "thread_policy": "REUSE",
        })

        assert websocket.name == "websocket"
        assert await websocket.start() is None
        first = await websocket.send_text(
            external_key="ws:user-1",
            recipient_actor_id="worker",
            text="first message",
        )
        second = await websocket.send_text(
            external_key="ws:user-1",
            recipient_actor_id="worker",
            text="second message",
        )
        assert await websocket.stop() is None

        sessions = await gateway.query_sessions(
            surface="websocket",
            external_key="ws:user-1",
        )
        pending = await gateway.list_pending_deliveries("worker")

        assert first["thread_id"] == second["thread_id"]
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] == first["thread_id"]
        assert len(pending) == 2

        with pytest.raises(
            ValidationError,
            match="binding is not registered: websocket:missing",
        ):
            await websocket.send_text(
                external_key="missing",
                recipient_actor_id="worker",
                text="no binding",
            )
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_connects_attachment_and_disconnects_presence(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-connection.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await gateway.register_binding({
            "id": "binding-ws-user",
            "surface": "websocket",
            "actor_id": "ws-user",
            "external_key": "ws:user-live",
            "thread_policy": "REUSE",
        })

        await websocket.start()
        connection = await websocket.connect(
            external_key="ws:user-live",
            connection_id="conn-1",
            metadata={"client": "browser"},
            capabilities=["ingress", "egress", "presence"],
        )
        state = await websocket.get_connection("conn-1")
        attachments = await gateway.query_attachments(actor_id="ws-user", kind="CONNECTOR")
        presence = await gateway.get_presence("websocket:conn-1")

        assert connection == {
            "connection_id": "conn-1",
            "actor_id": "ws-user",
            "attachment_id": "websocket:conn-1",
            "external_key": "ws:user-live",
            "thread_id": None,
        }
        assert state is not None
        assert state["metadata"] == {"client": "browser"}
        assert state["capabilities"] == ["ingress", "egress", "presence"]
        assert len(attachments) == 1
        assert attachments[0]["endpoint"]["connection_id"] == "conn-1"
        assert presence is not None
        assert presence["status"] == "ONLINE"

        await websocket.disconnect("conn-1")
        detached_presence = await gateway.get_presence("websocket:conn-1")

        assert await websocket.get_connection("conn-1") is None
        assert detached_presence is not None
        assert detached_presence["status"] == "OFFLINE"
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_requires_start_and_stop_disconnects_active_connections(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-start-stop.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })

        with pytest.raises(RuntimeError, match="WebSocketConnector 尚未启动"):
            await websocket.connect(
                external_key="ws:user-live",
                actor_id="ws-user",
                connection_id="conn-stop",
            )

        await websocket.start()
        await websocket.connect(
            external_key="ws:user-live",
            actor_id="ws-user",
            connection_id="conn-stop",
        )
        await websocket.disconnect("missing")
        await websocket.stop()

        assert await websocket.get_connection("conn-stop") is None
        presence = await gateway.get_presence("websocket:conn-stop")
        assert presence is not None
        assert presence["status"] == "OFFLINE"
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_cleans_up_when_transport_open_fails(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-open-fail.db"))
    )
    websocket = WebSocketConnector(
        gateway,
        transport=FailingOpenTransport(),
    )

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })

        await websocket.start()
        with pytest.raises(RuntimeError, match="transport open failed"):
            await websocket.connect(
                external_key="ws:user-live",
                actor_id="ws-user",
                connection_id="conn-fail",
            )

        assert await websocket.get_connection("conn-fail") is None
        presence = await gateway.get_presence("websocket:conn-fail")
        assert presence is not None
        assert presence["status"] == "OFFLINE"
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_receive_once_drains_actor_deliveries(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-receive.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await websocket.start()
        await websocket.connect(
            external_key="ws:user-live",
            actor_id="ws-user",
            connection_id="conn-recv",
        )

        sent = await gateway.ingest({
            "sender_actor_id": "tyrant",
            "recipient_actor_ids": ["ws-user"],
            "payload": {"text": "server push"},
            "kind": "CHAT",
            "caller_actor_id": "tyrant",
        })

        messages = await websocket.receive_once(connection_id="conn-recv", limit=10)
        pending = await gateway.list_pending_deliveries("ws-user")

        assert [item["envelope"]["payload"]["text"] for item in messages] == ["server push"]
        assert messages[0]["envelope"]["id"] == sent["envelope"]["id"]
        assert pending == []

        with pytest.raises(
            ValidationError,
            match="websocket connection is not active: missing",
        ):
            await websocket.receive_once(connection_id="missing")
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_pump_validation_and_empty_routes(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-pump-errors.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await websocket.start()
        await websocket.connect(
            external_key="ws:user-live",
            actor_id="ws-user",
            connection_id="conn-pump",
        )

        with pytest.raises(
            ValidationError,
            match="websocket connection is not active: missing",
        ):
            await websocket.pump_outbound_once(connection_id="missing")

        with pytest.raises(
            ValidationError,
            match="actor_id does not match websocket connection owner",
        ):
            await websocket.pump_outbound_once(
                actor_id="other-actor",
                connection_id="conn-pump",
            )

        with pytest.raises(
            ValidationError,
            match="actor_id or connection_id is required",
        ):
            await websocket.pump_outbound_once()

        assert await websocket.pump_outbound_once(actor_id="no-live-connection") == []
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_pump_breaks_when_route_disappears_after_leasing(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-route-drop.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await websocket.start()
        await websocket.connect(
            external_key="ws:user-live",
            actor_id="ws-user",
            connection_id="conn-route-drop",
        )
        route = websocket.connections.get("conn-route-drop")
        assert route is not None
        websocket.router = EmptyAfterLeaseRouter(route)

        sent = await gateway.ingest({
            "sender_actor_id": "tyrant",
            "recipient_actor_ids": ["ws-user"],
            "payload": {"text": "route disappears"},
            "kind": "CHAT",
            "caller_actor_id": "tyrant",
        })

        delivered = await websocket.pump_outbound_once(actor_id="ws-user")
        messages = await websocket.transport.receive_once(connection_id="conn-route-drop")
        leased = await gateway.query_deliveries(actor_id="ws-user", state="LEASED")

        assert delivered == []
        assert messages == []
        assert [item["envelope_id"] for item in leased] == [sent["envelope"]["id"]]
    finally:
        await gateway.stop()


async def test_v2_in_memory_websocket_transport_buffers_messages_per_connection() -> None:
    transport = InMemoryWebSocketTransport()

    await transport.start()
    await transport.open(connection_id="conn-1", connection={"actor_id": "actor-1"})
    await transport.open(connection_id="conn-2", connection={"actor_id": "actor-1"})

    await transport.send(connection_id="conn-1", message={"text": "hello-1"})
    await transport.send(connection_id="conn-2", message={"text": "hello-2"})

    assert await transport.receive_once(connection_id="conn-1") == [{"text": "hello-1"}]
    assert await transport.receive_once(connection_id="conn-2") == [{"text": "hello-2"}]

    await transport.close("conn-1")
    with pytest.raises(
        ValidationError,
        match="websocket transport connection is not active: conn-1",
    ):
        await transport.receive_once(connection_id="conn-1")

    await transport.stop()


def test_v2_websocket_delivery_router_supports_requested_last_active_and_broadcast() -> None:
    registry = WebSocketConnectionRegistry()
    now = datetime.now(timezone.utc)
    first = WebSocketConnection(
        connection_id="conn-1",
        actor_id="actor-1",
        attachment_id="websocket:conn-1",
        external_key="ws:user-1",
        account_id=None,
        thread_id=None,
        metadata={},
        capabilities=["ingress", "egress"],
        connected_at=now,
        last_seen_at=now,
    )
    second = WebSocketConnection(
        connection_id="conn-2",
        actor_id="actor-1",
        attachment_id="websocket:conn-2",
        external_key="ws:user-1",
        account_id=None,
        thread_id=None,
        metadata={},
        capabilities=["ingress", "egress"],
        connected_at=now + timedelta(seconds=1),
        last_seen_at=now + timedelta(seconds=1),
    )
    registry.add(first)
    registry.add(second)

    prefer_requested = WebSocketDeliveryRouter(registry, mode="PREFER_REQUESTED")
    assert [
        item.connection_id
        for item in prefer_requested.route(
            actor_id="actor-1",
            preferred_connection_id="conn-1",
        )
    ] == ["conn-1"]

    registry.touch("conn-2")
    last_active = WebSocketDeliveryRouter(registry, mode="LAST_ACTIVE")
    assert [
        item.connection_id for item in last_active.route(actor_id="actor-1")
    ] == ["conn-2"]

    broadcast = WebSocketDeliveryRouter(registry, mode="BROADCAST")
    assert [
        item.connection_id for item in broadcast.route(actor_id="actor-1")
    ] == ["conn-1", "conn-2"]

    first_active = WebSocketDeliveryRouter(registry, mode="FIRST_ACTIVE")
    assert [
        item.connection_id for item in first_active.route(actor_id="actor-1")
    ] == ["conn-1"]

    fallback = WebSocketDeliveryRouter(registry, mode="PREFER_REQUESTED")
    assert [
        item.connection_id
        for item in fallback.route(
            actor_id="actor-1",
            preferred_connection_id="missing",
        )
    ] == ["conn-2"]
    assert fallback.route(actor_id="missing-actor") == []

    with pytest.raises(
        ValidationError,
        match="unsupported websocket routing mode: INVALID",
    ):
        WebSocketDeliveryRouter(registry, mode="INVALID")  # type: ignore[arg-type]

    with pytest.raises(
        ValidationError,
        match="websocket connection already exists: conn-1",
    ):
        registry.add(first)

    assert registry.remove("missing") is None
    assert registry.touch("missing") is None


async def test_v2_websocket_connector_prefers_requested_connection_for_multi_connection_actor(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-multi-route.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await websocket.start()
        await websocket.connect(
            external_key="ws:user-live",
            actor_id="ws-user",
            connection_id="conn-1",
        )
        await websocket.connect(
            external_key="ws:user-live",
            actor_id="ws-user",
            connection_id="conn-2",
        )

        first = await gateway.ingest({
            "sender_actor_id": "tyrant",
            "recipient_actor_ids": ["ws-user"],
            "payload": {"text": "first server push"},
            "kind": "CHAT",
            "caller_actor_id": "tyrant",
        })

        first_conn_messages = await websocket.receive_once(connection_id="conn-1")
        second_conn_messages = await websocket.receive_once(connection_id="conn-2")

        assert [item["envelope"]["id"] for item in first_conn_messages] == [
            first["envelope"]["id"]
        ]
        assert second_conn_messages == []

        second = await gateway.ingest({
            "sender_actor_id": "tyrant",
            "recipient_actor_ids": ["ws-user"],
            "payload": {"text": "second server push"},
            "kind": "CHAT",
            "caller_actor_id": "tyrant",
        })

        second_conn_messages = await websocket.receive_once(connection_id="conn-2")
        first_conn_follow_up = await websocket.receive_once(connection_id="conn-1")

        assert [item["envelope"]["id"] for item in second_conn_messages] == [
            second["envelope"]["id"]
        ]
        assert first_conn_follow_up == []
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_send_payload_uses_ingress_path(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-send-payload.db"))
    )
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })
        await gateway.register_binding({
            "id": "binding-ws-user",
            "surface": "websocket",
            "actor_id": "ws-user",
            "external_key": "ws:user-payload",
            "thread_policy": "REUSE",
        })

        await websocket.start()
        result = await websocket.send_payload(
            external_key="ws:user-payload",
            recipient_actor_id="worker",
            payload={"text": "payload message", "kind": "structured"},
        )
        pending = await gateway.list_pending_deliveries("worker")

        assert result["envelope"]["payload"] == {
            "text": "payload message",
            "kind": "structured",
        }
        assert len(pending) == 1
        assert pending[0]["envelope_id"] == result["envelope"]["id"]
    finally:
        await gateway.stop()


async def test_v2_in_memory_websocket_transport_send_requires_active_connection() -> None:
    transport = InMemoryWebSocketTransport()

    await transport.start()
    with pytest.raises(
        ValidationError,
        match="websocket transport connection is not active: conn-missing",
    ):
        await transport.send(connection_id="conn-missing", message={"text": "hello"})
    await transport.stop()


async def test_v2_direct_websocket_transport_binds_sends_and_closes_socket() -> None:
    transport = DirectWebSocketTransport()
    socket = FakeWebSocket()

    await transport.start()
    await transport.bind_socket(connection_id="conn-direct", socket=socket)
    await transport.open(
        connection_id="conn-direct",
        connection={"actor_id": "actor-1"},
    )
    await transport.send(
        connection_id="conn-direct",
        message={"type": "delivery", "payload": {"text": "hello"}},
    )

    assert socket.sent == [{"type": "delivery", "payload": {"text": "hello"}}]
    assert await transport.receive_once(connection_id="conn-direct") == []

    await transport.close("conn-direct")
    assert socket.closed == 1

    with pytest.raises(
        ValidationError,
        match="websocket transport connection is not active: conn-direct",
    ):
        await transport.send(connection_id="conn-direct", message={"text": "x"})


async def test_v2_websocket_server_shell_start_stop_and_report_bound_url(tmp_path) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-shell-start.db"))
    )
    transport = DirectWebSocketTransport()
    connector = WebSocketConnector(gateway, transport=transport)
    serve = FakeServeFactory(host="127.0.0.1", port=9011)
    shell = WebSocketServerShell(
        connector,
        transport=transport,
        host="127.0.0.1",
        port=0,
        serve_fn=serve,
    )

    await gateway.start()
    try:
        await shell.start()
        assert shell.url() == "ws://127.0.0.1:9011"
        assert serve.calls == [{"host": "127.0.0.1", "port": 0, "kwargs": {}}]

        await shell.stop()
        assert serve.server.closed == 1
        assert serve.server.waited == 1
        assert shell.url() is None
    finally:
        await gateway.stop()


async def test_v2_websocket_server_shell_handles_connect_send_text_and_disconnect(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-shell-send.db"))
    )
    transport = DirectWebSocketTransport()
    connector = WebSocketConnector(gateway, transport=transport)
    shell = WebSocketServerShell(
        connector,
        transport=transport,
        serve_fn=FakeServeFactory(),
        poll_interval=0.01,
    )
    socket = FakeWebSocket([
        {
            "type": "connect",
            "external_key": "ws:user-shell",
            "actor_id": "ws-user",
            "connection_id": "conn-shell-send",
        },
        {
            "type": "send_text",
            "recipient_actor_id": "worker",
            "text": "hello shell",
        },
        {"type": "disconnect"},
    ])

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })

        await shell.start()
        await shell._handle_socket(socket)

        pending = await gateway.list_pending_deliveries("worker")

        assert [item["type"] for item in socket.sent] == [
            "connected",
            "result",
            "disconnecting",
        ]
        assert socket.sent[1]["action"] == "send_text"
        assert pending[0]["recipient_actor_id"] == "worker"
    finally:
        await gateway.stop()


async def test_v2_websocket_server_shell_egress_loop_pushes_delivery_to_socket(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-shell-egress.db"))
    )
    transport = DirectWebSocketTransport()
    connector = WebSocketConnector(gateway, transport=transport)
    shell = WebSocketServerShell(
        connector,
        transport=transport,
        serve_fn=FakeServeFactory(),
        poll_interval=0.01,
    )
    socket = FakeWebSocket([
        {
            "type": "connect",
            "external_key": "ws:user-egress",
            "actor_id": "ws-user",
            "connection_id": "conn-shell-egress",
        }
    ])

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })

        await shell.start()
        task = asyncio.create_task(shell._handle_socket(socket))

        for _ in range(50):
            if socket.sent:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("websocket shell did not complete connect handshake in time")

        await gateway.ingest({
            "sender_actor_id": "tyrant",
            "recipient_actor_ids": ["ws-user"],
            "payload": {"text": "server push"},
            "kind": "CHAT",
            "caller_actor_id": "tyrant",
        })

        for _ in range(50):
            if any(item["type"] == "delivery" for item in socket.sent):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("websocket shell did not push delivery in time")

        await socket.feed({"type": "disconnect"})
        await task

        delivery = next(item for item in socket.sent if item["type"] == "delivery")
        assert delivery["envelope"]["payload"]["text"] == "server push"
    finally:
        await gateway.stop()


async def test_v2_websocket_server_shell_returns_error_for_invalid_first_message(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-shell-error.db"))
    )
    transport = DirectWebSocketTransport()
    connector = WebSocketConnector(gateway, transport=transport)
    shell = WebSocketServerShell(
        connector,
        transport=transport,
        serve_fn=FakeServeFactory(),
    )
    socket = FakeWebSocket([
        {
            "type": "send_text",
            "recipient_actor_id": "worker",
            "text": "bad order",
        }
    ])

    await gateway.start()
    try:
        await shell.start()
        await shell._handle_socket(socket)

        assert socket.sent[0]["type"] == "error"
        assert socket.sent[0]["error"]["message"] == "first websocket message must be type=connect"
        assert socket.closed >= 1
    finally:
        await gateway.stop()


def test_v2_websocket_server_shell_requires_direct_or_matching_transport(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-shell-init.db"))
    )
    connector = WebSocketConnector(gateway)

    with pytest.raises(
        ValidationError,
        match="WebSocketServerShell requires DirectWebSocketTransport",
    ):
        WebSocketServerShell(connector)

    with pytest.raises(
        ValidationError,
        match="connector.transport must match WebSocketServerShell transport",
    ):
        WebSocketServerShell(
            connector,
            transport=DirectWebSocketTransport(),
        )


async def test_v2_websocket_server_shell_start_failure_stops_connector() -> None:
    transport = DirectWebSocketTransport()
    connector = FakeShellConnector(transport)
    shell = WebSocketServerShell(
        connector,  # type: ignore[arg-type]
        transport=transport,
        serve_fn=FailingServeFactory(),
    )

    with pytest.raises(RuntimeError, match="serve failed"):
        await shell.start()

    assert connector.started == 1
    assert connector.stopped == 1


async def test_v2_websocket_server_shell_stop_without_running_server_is_safe() -> None:
    transport = DirectWebSocketTransport()
    connector = FakeShellConnector(transport)
    shell = WebSocketServerShell(
        connector,  # type: ignore[arg-type]
        transport=transport,
    )

    await shell.stop()

    assert connector.stopped == 1
    assert shell.url() is None


async def test_v2_websocket_server_shell_open_connection_validates_and_unbinds_on_failure() -> None:
    transport = DirectWebSocketTransport()
    connector = FakeShellConnector(
        transport,
        connect_error=ValidationError("connect failed"),
    )
    shell = WebSocketServerShell(
        connector,  # type: ignore[arg-type]
        transport=transport,
    )
    socket = FakeWebSocket()

    with pytest.raises(ValidationError, match="connect.external_key is required"):
        await shell._open_connection(socket, {"type": "connect"})

    with pytest.raises(
        ValidationError,
        match="connect.connection_id must be a non-empty string",
    ):
        await shell._open_connection(
            socket,
            {"type": "connect", "external_key": "ws:user", "connection_id": 1},
        )

    with pytest.raises(ValidationError, match="connect failed"):
        await shell._open_connection(
            socket,
            {
                "type": "connect",
                "external_key": "ws:user",
                "connection_id": "conn-open-fail",
            },
        )

    with pytest.raises(
        ValidationError,
        match="websocket transport connection is not active: conn-open-fail",
    ):
        await transport.send(
            connection_id="conn-open-fail",
            message={"type": "x"},
        )


async def test_v2_websocket_server_shell_open_connection_requires_state_after_connect() -> None:
    transport = DirectWebSocketTransport()
    connector = FakeShellConnector(
        transport,
        connection_state=None,
    )
    shell = WebSocketServerShell(
        connector,  # type: ignore[arg-type]
        transport=transport,
    )
    socket = FakeWebSocket()

    with pytest.raises(
        ValidationError,
        match="websocket connection state is missing after connect",
    ):
        await shell._open_connection(
            socket,
            {
                "type": "connect",
                "external_key": "ws:user",
                "connection_id": "conn-missing-state",
            },
        )


async def test_v2_websocket_server_shell_dispatch_helpers_and_recv_json_edges() -> None:
    transport = DirectWebSocketTransport()
    connector = FakeShellConnector(
        transport,
        send_payload_result={"thread_id": "thread-payload", "envelope": {"id": "env-payload"}},
    )
    shell = WebSocketServerShell(
        connector,  # type: ignore[arg-type]
        transport=transport,
    )
    context = type(
        "Ctx",
        (),
        {
            "connection_id": "conn-ctx",
            "actor_id": "actor-ctx",
            "external_key": "ws:user-ctx",
            "account_id": "acct-ctx",
            "thread_id": None,
        },
    )()

    assert await shell._dispatch_message(context, {"type": "ping"}) == ({"type": "pong"}, False)

    response, should_close = await shell._dispatch_message(
        context,
        {
            "type": "send_payload",
            "recipient_actor_id": "worker",
            "payload": {"text": "payload"},
        },
    )
    assert should_close is False
    assert response == {
        "type": "result",
        "action": "send_payload",
        "result": {"thread_id": "thread-payload", "envelope": {"id": "env-payload"}},
    }
    assert context.thread_id == "thread-payload"

    response, should_close = await shell._dispatch_message(
        context,
        {"type": "disconnect"},
    )
    assert should_close is True
    assert response == {"type": "disconnecting", "connection_id": "conn-ctx"}

    with pytest.raises(ValidationError, match="send_text.text is required"):
        await shell._dispatch_message(context, {"type": "send_text"})

    with pytest.raises(ValidationError, match="unsupported websocket action: nope"):
        await shell._dispatch_message(context, {"type": "nope"})

    bytes_socket = FakeWebSocket()
    await bytes_socket.feed({"type": "ping"})
    assert await shell._recv_json(bytes_socket) == {"type": "ping"}

    class BinarySocket(FakeWebSocket):
        async def recv(self):
            return json.dumps({"type": "ping"}).encode("utf-8")

    assert await shell._recv_json(BinarySocket()) == {"type": "ping"}

    class IntSocket(FakeWebSocket):
        async def recv(self):
            return 1

    with pytest.raises(ValidationError, match="websocket message must be text"):
        await shell._recv_json(IntSocket())

    class InvalidJsonSocket(FakeWebSocket):
        async def recv(self):
            return "{bad json"

    with pytest.raises(ValidationError, match="websocket message must be valid json"):
        await shell._recv_json(InvalidJsonSocket())

    class ListJsonSocket(FakeWebSocket):
        async def recv(self):
            return "[]"

    with pytest.raises(ValidationError, match="websocket message must be a json object"):
        await shell._recv_json(ListJsonSocket())

    with pytest.raises(ValidationError, match="field must be a json object"):
        shell._maybe_dict("x", "field")
    with pytest.raises(ValidationError, match="field must be a list\\[str\\]"):
        shell._maybe_str_list(["ok", 1], "field")
    assert shell._maybe_dict(None, "field") is None
    assert shell._maybe_str_list(None, "field") is None


async def test_v2_websocket_server_shell_auxiliary_error_close_and_capture_paths() -> None:
    transport = DirectWebSocketTransport()
    connector = FakeShellConnector(transport, pump_error=ValidationError("stopped"))
    shell = WebSocketServerShell(
        connector,  # type: ignore[arg-type]
        transport=transport,
        host="0.0.0.0",
        port=8765,
    )
    socket = FakeWebSocket()
    failing_socket = FailingSendWebSocket()

    await shell._safe_send_error(socket, None, ValidationError("bad"))
    assert socket.sent == [
        {
            "type": "error",
            "error": {"kind": "ValidationError", "message": "bad"},
        }
    ]

    await transport.bind_socket(connection_id="conn-error", socket=failing_socket)
    await transport.open(connection_id="conn-error", connection={"actor_id": "actor-1"})
    context = type("Ctx", (), {"connection_id": "conn-error"})()
    await shell._safe_send_error(failing_socket, context, ValidationError("bad"))

    await shell._safe_close(FailingCloseWebSocket())
    await shell._egress_loop("conn-missing")
    shell._capture_bound_address()
    assert shell.url() == "ws://0.0.0.0:8765"


async def test_v2_direct_websocket_transport_validation_edges() -> None:
    transport = DirectWebSocketTransport()
    socket = FakeWebSocket()

    await transport.bind_socket(connection_id="conn-x", socket=socket)
    with pytest.raises(
        ValidationError,
        match="websocket socket is already bound: conn-x",
    ):
        await transport.bind_socket(connection_id="conn-x", socket=socket)

    await transport.unbind_socket("conn-x")
    with pytest.raises(
        ValidationError,
        match="websocket socket is not bound: conn-x",
    ):
        await transport.open(connection_id="conn-x", connection={"actor_id": "a"})

    assert await transport.close("missing") is None
