"""Gateway 服务入口。"""

from __future__ import annotations

from tyrant_core.api import SystemAPI
from tyrant_core.gateway.application import GatewayApplication
from tyrant_core.gateway.binding_resolver import BindingResolver
from tyrant_core.gateway.control_plane import GatewayControlPlane
from tyrant_core.gateway.delivery_engine import DeliveryEngine
from tyrant_core.gateway.manager import GatewayManager, ManagedService
from tyrant_core.gateway.session_registry import SessionRegistry
from tyrant_core.management import ActorReconcileWorker
from tyrant_core.models import UnsupportedLegacyComponentError
from tyrant_core.provisioning import ProvisioningFactoryWorker
from tyrant_core.store import SqliteStore, Store


class GatewayServer:
    """组合 Gateway 关键子系统。"""

    def __init__(
        self,
        *,
        store: Store | None = None,
        api: SystemAPI | None = None,
        manager: GatewayManager | None = None,
        bindings: BindingResolver | None = None,
        sessions: SessionRegistry | None = None,
        deliveries: DeliveryEngine | None = None,
        application: GatewayApplication | None = None,
        control: GatewayControlPlane | None = None,
    ) -> None:
        self.store = store or SqliteStore()
        self.api = api or SystemAPI(store=self.store)
        self.manager = manager or GatewayManager()
        self.bindings = bindings or BindingResolver(self.store)
        self.sessions = sessions or SessionRegistry(self.store)
        self.deliveries = deliveries or DeliveryEngine(self.store)
        self.application = application or GatewayApplication(self.api)
        self.control = control or GatewayControlPlane(
            api=self.api,
            bindings=self.bindings,
            sessions=self.sessions,
            deliveries=self.deliveries,
        )
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.store.initialize()
        await self.manager.start_all()
        self._started = True

    async def stop(self) -> None:
        try:
            await self.manager.stop_all()
        finally:
            await self.store.close()
            self._started = False

    def register_managed_service(self, service: ManagedService) -> None:
        self.manager.register(service)

    def register_factory_worker(
        self,
        *,
        owner_actor_id: str,
        executor_actor_id: str | None = None,
        poll_interval: float = 1.0,
        auto_reconcile: bool = True,
        scan_limit: int = 20,
        name: str | None = None,
    ) -> ProvisioningFactoryWorker:
        if self.api.provisioning_queue is None:
            raise UnsupportedLegacyComponentError("provisioning queue is not configured")
        worker = ProvisioningFactoryWorker(
            self.api.provisioning_queue,
            owner_actor_id=owner_actor_id,
            executor_actor_id=executor_actor_id,
            poll_interval=poll_interval,
            auto_reconcile=auto_reconcile,
            scan_limit=scan_limit,
            name=name,
        )
        self.register_managed_service(worker)
        return worker

    def register_provisioning_worker(
        self,
        *,
        owner_actor_id: str,
        executor_actor_id: str | None = None,
        poll_interval: float = 1.0,
        auto_reconcile: bool = True,
        scan_limit: int = 20,
        name: str | None = None,
    ) -> ProvisioningFactoryWorker:
        """按新命名注册创建队列 worker。"""
        return self.register_factory_worker(
            owner_actor_id=owner_actor_id,
            executor_actor_id=executor_actor_id,
            poll_interval=poll_interval,
            auto_reconcile=auto_reconcile,
            scan_limit=scan_limit,
            name=name,
        )

    def register_reconcile_worker(
        self,
        *,
        poll_interval: float = 1.0,
        limit: int = 1000,
        name: str = "reconcile-worker",
    ) -> ActorReconcileWorker:
        if self.api.reconciler is None:
            raise UnsupportedLegacyComponentError("actor reconciler is not configured")
        worker = ActorReconcileWorker(
            self.api.reconciler,
            poll_interval=poll_interval,
            limit=limit,
            name=name,
        )
        self.register_managed_service(worker)
        return worker

    def register_websocket_server(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        surface: str = "websocket",
        gateway_id: str = "gateway:websocket",
        routing_mode: str = "PREFER_REQUESTED",
        poll_interval: float = 0.05,
        egress_limit: int = 20,
        lease_seconds: int = 30,
        serve_fn=None,
        serve_kwargs: dict | None = None,
        name: str = "websocket-server",
    ):
        """注册一个真实 websocket 网络壳层。"""
        from tyrant_core.gateway.connectors import (
            DirectWebSocketTransport,
            WebSocketConnector,
            WebSocketServerShell,
        )

        transport = DirectWebSocketTransport()
        connector = WebSocketConnector(
            self,
            surface=surface,
            gateway_id=gateway_id,
            transport=transport,
            routing_mode=routing_mode,
        )
        server = WebSocketServerShell(
            connector,
            transport=transport,
            host=host,
            port=port,
            poll_interval=poll_interval,
            egress_limit=egress_limit,
            lease_seconds=lease_seconds,
            serve_fn=serve_fn,
            serve_kwargs=serve_kwargs,
            name=name,
        )
        self.register_managed_service(server)
        return server

    async def create_actor(self, spec: dict) -> dict:
        return await self.application.create_actor(spec)

    async def get_actor(self, actor_id: str) -> dict:
        return await self.application.get_actor(actor_id)

    async def list_actors(self, **filters) -> list[dict]:
        return await self.application.list_actors(**filters)

    async def create_org_edge(self, spec: dict) -> dict:
        return await self.application.create_org_edge(spec)

    async def list_org_edges(self, **filters) -> list[dict]:
        return await self.application.list_org_edges(**filters)

    async def issue_grant(self, spec: dict) -> dict:
        return await self.application.issue_grant(spec)

    async def list_grants(self, subject_actor_id: str) -> list[dict]:
        return await self.application.list_grants(subject_actor_id)

    async def open_thread(self, spec: dict) -> dict:
        return await self.application.open_thread(spec)

    async def get_thread(self, thread_id: str) -> dict:
        return await self.application.get_thread(thread_id)

    async def ingest(self, spec: dict) -> dict:
        return await self.application.ingest(spec)

    async def get_envelope(self, envelope_id: str) -> dict:
        return await self.application.get_envelope(envelope_id)

    async def query_envelopes(
        self,
        *,
        thread_id: str | None = None,
        sender_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.application.query_envelopes(
            thread_id=thread_id,
            sender_actor_id=sender_actor_id,
            limit=limit,
        )

    async def create_work_item(self, spec: dict) -> dict:
        return await self.application.create_work_item(spec)

    async def create_actor_profile(self, spec: dict) -> dict:
        return await self.application.create_actor_profile(spec)

    async def get_actor_profile(self, profile_id: str) -> dict | None:
        return await self.application.get_actor_profile(profile_id)

    async def query_actor_profiles(
        self,
        *,
        archived: bool | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.application.query_actor_profiles(
            archived=archived,
            limit=limit,
        )

    async def create_actor_control(self, spec: dict) -> dict:
        return await self.application.create_actor_control(spec)

    async def get_actor_control(self, actor_id: str) -> dict | None:
        return await self.application.get_actor_control(actor_id)

    async def query_actor_controls(
        self,
        *,
        manager_actor_id: str | None = None,
        desired_state: str | None = None,
        runtime_kind: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.application.query_actor_controls(
            manager_actor_id=manager_actor_id,
            desired_state=desired_state,
            runtime_kind=runtime_kind,
            limit=limit,
        )

    async def assign_actor_manager(self, actor_id: str, manager_actor_id: str) -> dict:
        return await self.application.assign_actor_manager(actor_id, manager_actor_id)

    async def set_actor_profile(self, actor_id: str, profile_id: str | None) -> dict:
        return await self.application.set_actor_profile(actor_id, profile_id)

    async def set_actor_desired_state(self, actor_id: str, desired_state: str) -> dict:
        return await self.application.set_actor_desired_state(actor_id, desired_state)

    async def submit_provision_request(self, spec: dict) -> dict:
        return await self.application.submit_provision_request(spec)

    async def get_provision_request(self, request_id: str) -> dict | None:
        return await self.application.get_provision_request(request_id)

    async def claim_provision_request(
        self,
        request_id: str,
        executor_actor_id: str,
    ) -> bool:
        return await self.application.claim_provision_request(
            request_id,
            executor_actor_id,
        )

    async def query_provision_requests(self, **filters) -> list[dict]:
        return await self.application.query_provision_requests(**filters)

    async def execute_provision_request(
        self,
        request_id: str,
        *,
        executor_actor_id: str | None = None,
    ) -> dict:
        return await self.application.execute_provision_request(
            request_id,
            executor_actor_id=executor_actor_id,
        )

    async def get_provision_record(self, record_id: str) -> dict | None:
        return await self.application.get_provision_record(record_id)

    async def query_provision_records(self, **filters) -> list[dict]:
        return await self.application.query_provision_records(**filters)

    async def request_actor_provision(self, spec: dict) -> dict:
        return await self.application.request_actor_provision(spec)

    async def process_provision_work_item(
        self,
        work_item_id: str,
        *,
        executor_actor_id: str,
        auto_reconcile: bool = True,
    ) -> dict:
        return await self.application.process_provision_work_item(
            work_item_id,
            executor_actor_id=executor_actor_id,
            auto_reconcile=auto_reconcile,
        )

    async def process_next_provision_work_item(
        self,
        *,
        owner_actor_id: str,
        executor_actor_id: str,
        auto_reconcile: bool = True,
        scan_limit: int = 20,
    ) -> dict | None:
        return await self.application.process_next_provision_work_item(
            owner_actor_id=owner_actor_id,
            executor_actor_id=executor_actor_id,
            auto_reconcile=auto_reconcile,
            scan_limit=scan_limit,
        )

    async def spawn_actor_runtime(self, actor_id: str) -> dict:
        return await self.application.spawn_actor_runtime(actor_id)

    async def stop_actor_runtime(self, actor_id: str) -> None:
        await self.application.stop_actor_runtime(actor_id)

    async def get_runtime_status(self, actor_id: str) -> dict | None:
        return await self.application.get_runtime_status(actor_id)

    async def reconcile_actor(self, actor_id: str) -> bool:
        return await self.application.reconcile_actor(actor_id)

    async def reconcile_all(self, *, limit: int = 1000) -> int:
        return await self.application.reconcile_all(limit=limit)

    async def get_work_item(self, work_item_id: str) -> dict:
        return await self.application.get_work_item(work_item_id)

    async def query_work_items(
        self,
        *,
        owner_actor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.application.query_work_items(
            owner_actor_id=owner_actor_id,
            status=status,
            limit=limit,
        )

    async def get_domain_events(self, *, since=None, limit: int = 100) -> list[dict]:
        return await self.application.get_domain_events(since=since, limit=limit)

    async def can(
        self,
        action: str,
        subject_actor_id: str,
        target_id: str | None = None,
    ):
        return await self.application.can(
            action,
            subject_actor_id,
            target_id=target_id,
        )

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
    ):
        return await self.control.open_session(
            surface=surface,
            actor_id=actor_id,
            external_key=external_key,
            thread_id=thread_id,
            mode=mode,
            binding_id=binding_id,
            metadata=metadata,
        )

    async def bind_session_thread(self, session_id: str, thread_id: str):
        return await self.control.bind_session_thread(session_id, thread_id)

    async def get_session(self, session_id: str) -> dict | None:
        return await self.control.get_session(session_id)

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.control.query_sessions(
            actor_id=actor_id,
            surface=surface,
            external_key=external_key,
            state=state,
            limit=limit,
        )

    async def register_binding(self, spec: dict) -> dict:
        return await self.control.register_binding(spec)

    async def query_bindings(
        self,
        *,
        surface: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.control.query_bindings(
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
    ) -> dict | None:
        return await self.control.resolve_binding(
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
    ) -> list[dict]:
        return await self.control.lease_deliveries(
            actor_id=actor_id,
            consumer_id=consumer_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def ack_delivery(self, delivery_id: str) -> None:
        await self.control.ack_delivery(delivery_id)

    async def fail_delivery(self, delivery_id: str, error: dict | None = None) -> None:
        await self.control.fail_delivery(delivery_id, error=error)

    async def list_pending_deliveries(self, actor_id: str) -> list[dict]:
        return await self.control.list_pending_deliveries(actor_id)

    async def query_deliveries(
        self,
        *,
        actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.control.query_deliveries(
            actor_id=actor_id,
            state=state,
            limit=limit,
        )

    async def register_attachment(self, spec: dict) -> dict:
        return await self.control.register_attachment(spec)

    async def report_presence(self, spec: dict) -> dict:
        return await self.control.report_presence(spec)

    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.control.query_attachments(
            actor_id=actor_id,
            kind=kind,
            limit=limit,
        )

    async def get_presence(self, attachment_id: str) -> dict | None:
        return await self.control.get_presence(attachment_id)
