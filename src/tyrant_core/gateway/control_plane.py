"""Gateway 控制平面 façade。"""

from __future__ import annotations

from tyrant_core.api import SystemAPI
from tyrant_core.gateway.binding_control import GatewayBindingControl
from tyrant_core.gateway.binding_resolver import BindingResolver
from tyrant_core.gateway.delivery_control import GatewayDeliveryControl
from tyrant_core.gateway.delivery_engine import DeliveryEngine
from tyrant_core.gateway.presence_control import GatewayPresenceControl
from tyrant_core.gateway.session_control import GatewaySessionControl
from tyrant_core.gateway.session_registry import GatewaySession, SessionRegistry


class GatewayControlPlane:
    """承接会话、binding、presence 与 delivery 控制。"""

    def __init__(
        self,
        *,
        api: SystemAPI,
        bindings: BindingResolver,
        sessions: SessionRegistry,
        deliveries: DeliveryEngine,
    ) -> None:
        self.api = api
        self.binding_resolver = bindings
        self.session_registry = sessions
        self.delivery_engine = deliveries
        self.session_control = GatewaySessionControl(sessions)
        self.binding_control = GatewayBindingControl(bindings)
        self.delivery_control = GatewayDeliveryControl(
            api=api,
            deliveries=deliveries,
        )
        self.presence_control = GatewayPresenceControl(api)

    async def open_session(
        self,
        *,
        surface: str,
        actor_id: str,
        external_key: str,
        thread_id: str | None = None,
        mode: str = "PERSISTENT",
        binding_id: str | None = None,
        metadata: dict | None = None,
    ) -> GatewaySession:
        return await self.session_control.open_session(
            surface=surface,
            actor_id=actor_id,
            external_key=external_key,
            thread_id=thread_id,
            mode=mode,
            binding_id=binding_id,
            metadata=metadata,
        )

    async def bind_session_thread(
        self,
        session_id: str,
        thread_id: str,
    ) -> GatewaySession | None:
        return await self.session_control.bind_session_thread(session_id, thread_id)

    async def get_session(self, session_id: str):
        return await self.session_control.get_session(session_id)

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ):
        return await self.session_control.query_sessions(
            actor_id=actor_id,
            surface=surface,
            external_key=external_key,
            state=state,
            limit=limit,
        )

    async def register_binding(self, spec: dict[str, object]):
        return await self.binding_control.register_binding(spec)

    async def query_bindings(
        self,
        *,
        surface: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ):
        return await self.binding_control.query_bindings(
            surface=surface,
            actor_id=actor_id,
            limit=limit,
        )

    async def resolve_binding(
        self,
        *,
        surface: str,
        external_key: str,
        account_id: str | None = None,
    ):
        return await self.binding_control.resolve_binding(
            surface=surface,
            external_key=external_key,
            account_id=account_id,
        )

    async def lease_deliveries(
        self,
        *,
        actor_id: str,
        consumer_id: str,
        limit: int = 1,
        lease_seconds: int = 30,
    ):
        return await self.delivery_control.lease_deliveries(
            actor_id=actor_id,
            consumer_id=consumer_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def ack_delivery(self, delivery_id: str) -> None:
        await self.delivery_control.ack_delivery(delivery_id)

    async def fail_delivery(self, delivery_id: str, error: dict | None = None) -> None:
        await self.delivery_control.fail_delivery(delivery_id, error=error)

    async def list_pending_deliveries(self, actor_id: str):
        return await self.delivery_control.list_pending_deliveries(actor_id)

    async def query_deliveries(
        self,
        *,
        actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ):
        return await self.delivery_control.query_deliveries(
            actor_id=actor_id,
            state=state,
            limit=limit,
        )

    async def register_attachment(self, spec: dict[str, object]):
        return await self.presence_control.register_attachment(spec)

    async def report_presence(self, spec: dict[str, object]):
        return await self.presence_control.report_presence(spec)

    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ):
        return await self.presence_control.query_attachments(
            actor_id=actor_id,
            kind=kind,
            limit=limit,
        )

    async def get_presence(self, attachment_id: str):
        return await self.presence_control.get_presence(attachment_id)
