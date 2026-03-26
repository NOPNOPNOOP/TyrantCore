"""WebSocket connector。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tyrant_core.gateway import GatewayServer
from tyrant_core.models import ValidationError

from .base import BoundIngressConnector
from .surface_lifecycle import SurfaceAttachmentLifecycle
from .websocket_connections import WebSocketConnection, WebSocketConnectionRegistry
from .websocket_routing import (
    WebSocketDeliveryRouter,
    WebSocketRoutingMode,
)
from .websocket_transport import InMemoryWebSocketTransport, WebSocketTransport


class WebSocketConnector(BoundIngressConnector):
    """把 websocket 输入接成 Gateway ingress。"""

    def __init__(
        self,
        gateway: GatewayServer,
        surface: str = "websocket",
        *,
        gateway_id: str = "gateway:websocket",
        connections: WebSocketConnectionRegistry | None = None,
        router: WebSocketDeliveryRouter | None = None,
        transport: WebSocketTransport | None = None,
        routing_mode: WebSocketRoutingMode = "PREFER_REQUESTED",
    ) -> None:
        super().__init__(
            gateway,
            surface=surface,
            name="websocket",
            default_kind="CHAT",
        )
        self.gateway_id = gateway_id
        self._started = False
        self.connections = connections or WebSocketConnectionRegistry()
        self.transport = transport or InMemoryWebSocketTransport()
        self.router = router or WebSocketDeliveryRouter(
            self.connections,
            mode=routing_mode,
        )

    async def start(self) -> None:
        await self.transport.start()
        self._started = True

    async def stop(self) -> None:
        for connection_id in self.connections.list_connection_ids():
            await self.disconnect(connection_id)
        await self.transport.stop()
        self._started = False

    async def connect(
        self,
        *,
        external_key: str,
        actor_id: str | None = None,
        account_id: str | None = None,
        connection_id: str | None = None,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self._started:
            raise RuntimeError("WebSocketConnector 尚未启动")

        effective_actor_id, _binding_id = await self._resolve_sender(
            external_key=external_key,
            sender_actor_id=actor_id,
            account_id=account_id,
        )
        effective_connection_id = connection_id or str(uuid4())
        effective_capabilities = list(capabilities or ["ingress", "egress"])
        attachment_id = f"websocket:{effective_connection_id}"
        endpoint = {
            "surface": self.surface,
            "external_key": external_key,
            "connection_id": effective_connection_id,
            "account_id": account_id,
            "thread_id": thread_id,
        }
        if metadata:
            endpoint["metadata"] = dict(metadata)
        lifecycle = SurfaceAttachmentLifecycle(
            gateway=self.gateway,
            actor_id=effective_actor_id,
            attachment_id=attachment_id,
            kind="CONNECTOR",
            endpoint=endpoint,
            capabilities=effective_capabilities,
            gateway_id=self.gateway_id,
        )
        await lifecycle.attach()
        now = datetime.now(timezone.utc)
        connection = WebSocketConnection(
            connection_id=effective_connection_id,
            actor_id=effective_actor_id,
            attachment_id=attachment_id,
            external_key=external_key,
            account_id=account_id,
            thread_id=thread_id,
            metadata=dict(metadata or {}),
            capabilities=effective_capabilities,
            connected_at=now,
            last_seen_at=now,
        )
        try:
            self.connections.add(connection)
            await self.transport.open(
                connection_id=effective_connection_id,
                connection=self._connection_payload(connection),
            )
        except Exception:
            self.connections.remove(effective_connection_id)
            await lifecycle.detach()
            raise
        return {
            "connection_id": effective_connection_id,
            "actor_id": effective_actor_id,
            "attachment_id": attachment_id,
            "external_key": external_key,
            "thread_id": thread_id,
        }

    async def disconnect(self, connection_id: str) -> None:
        connection = self.connections.remove(connection_id)
        if connection is None:
            return
        try:
            await self.transport.close(connection_id)
        finally:
            lifecycle = SurfaceAttachmentLifecycle(
                gateway=self.gateway,
                actor_id=connection.actor_id,
                attachment_id=connection.attachment_id,
                kind="CONNECTOR",
                endpoint={
                    "surface": self.surface,
                    "external_key": connection.external_key,
                    "connection_id": connection.connection_id,
                    "account_id": connection.account_id,
                    "thread_id": connection.thread_id,
                    "metadata": dict(connection.metadata),
                },
                capabilities=list(connection.capabilities),
                gateway_id=self.gateway_id,
            )
            await lifecycle.detach()

    async def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        return self.connections.snapshot(connection_id)

    async def pump_outbound_once(
        self,
        *,
        actor_id: str | None = None,
        connection_id: str | None = None,
        limit: int = 20,
        lease_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        preferred_connection_id = connection_id
        if connection_id is not None:
            connection = self.connections.touch(connection_id)
            if connection is None:
                raise ValidationError(f"websocket connection is not active: {connection_id}")
            if actor_id is not None and actor_id != connection.actor_id:
                raise ValidationError(
                    "actor_id does not match websocket connection owner"
                )
            actor_id = connection.actor_id

        if actor_id is None:
            raise ValidationError("actor_id or connection_id is required")

        routes = self.router.route(
            actor_id=actor_id,
            preferred_connection_id=preferred_connection_id,
        )
        if not routes:
            return []

        leased = await self.gateway.lease_deliveries(
            actor_id=actor_id,
            consumer_id=f"websocket:actor:{actor_id}",
            limit=limit,
            lease_seconds=lease_seconds,
        )
        delivered: list[dict[str, Any]] = []
        for item in leased:
            current_routes = self.router.route(
                actor_id=actor_id,
                preferred_connection_id=preferred_connection_id,
            )
            if not current_routes:
                break
            delivery = item["delivery"]
            envelope = await self.gateway.get_envelope(delivery["envelope_id"])
            message = {
                "type": "delivery",
                "delivery": delivery,
                "envelope": envelope,
            }
            for route in current_routes:
                await self.transport.send(
                    connection_id=route.connection_id,
                    message=message,
                )
            await self.gateway.ack_delivery(delivery["id"])
            delivered.append({
                "delivery_id": delivery["id"],
                "connection_ids": [route.connection_id for route in current_routes],
            })
        return delivered

    async def receive_once(
        self,
        *,
        connection_id: str,
        limit: int = 20,
        lease_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        connection = self.connections.get(connection_id)
        if connection is None:
            raise ValidationError(f"websocket connection is not active: {connection_id}")

        await self.pump_outbound_once(
            connection_id=connection_id,
            actor_id=connection.actor_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        return await self.transport.receive_once(
            connection_id=connection_id,
            limit=limit,
        )

    async def send_text(
        self,
        *,
        external_key: str,
        recipient_actor_id: str | None = None,
        recipient_actor_ids: list[str] | None = None,
        text: str,
        sender_actor_id: str | None = None,
        account_id: str | None = None,
        thread_id: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
        metadata: dict[str, Any] | None = None,
        bypass_policy: bool = True,
    ) -> dict[str, Any]:
        return await self.ingest_payload(
            external_key=external_key,
            payload={"text": text},
            recipient_actor_id=recipient_actor_id,
            recipient_actor_ids=recipient_actor_ids,
            sender_actor_id=sender_actor_id,
            account_id=account_id,
            thread_id=thread_id,
            kind=kind,
            subject=subject,
            metadata=metadata,
            bypass_policy=bypass_policy,
        )

    def _connection_payload(self, connection: WebSocketConnection) -> dict[str, Any]:
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

    async def send_payload(
        self,
        *,
        external_key: str,
        payload: dict[str, Any],
        recipient_actor_id: str | None = None,
        recipient_actor_ids: list[str] | None = None,
        sender_actor_id: str | None = None,
        account_id: str | None = None,
        thread_id: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
        metadata: dict[str, Any] | None = None,
        bypass_policy: bool = True,
    ) -> dict[str, Any]:
        return await self.ingest_payload(
            external_key=external_key,
            payload=payload,
            recipient_actor_id=recipient_actor_id,
            recipient_actor_ids=recipient_actor_ids,
            sender_actor_id=sender_actor_id,
            account_id=account_id,
            thread_id=thread_id,
            kind=kind,
            subject=subject,
            metadata=metadata,
            bypass_policy=bypass_policy,
        )
