from __future__ import annotations

import asyncio

from tyrant_core.api import SystemAPI
from tyrant_core.gateway import (
    GatewayApplication,
    GatewayBindingControl,
    GatewayControlPlane,
    GatewayDeliveryControl,
    GatewayPresenceControl,
    GatewayServer,
    GatewaySessionControl,
)
from tyrant_core.management import ActorReconciler
from tyrant_core.models import Attachment, AttachmentKind, Presence, PresenceStatus
from tyrant_core.runtime.driver_registry import build_single_runtime_driver_registry
from tyrant_core.runtime.runtime_driver import RuntimeSpawnResult
from tyrant_core.runtime.runtime_supervisor import RuntimeSupervisor
from tyrant_core.store import SqliteStore


class _FakeBoundSocket:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def getsockname(self) -> tuple[str, int]:
        return (self._host, self._port)


class _FakeWebSocketRuntimeServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9012) -> None:
        self.sockets = [_FakeBoundSocket(host, port)]
        self.closed = 0
        self.waited = 0

    def close(self) -> None:
        self.closed += 1

    async def wait_closed(self) -> None:
        self.waited += 1


class _FakeWebSocketServeFactory:
    def __init__(self) -> None:
        self.server = _FakeWebSocketRuntimeServer()
        self.calls: list[dict] = []

    async def __call__(self, handler, host: str, port: int, **kwargs):
        del handler
        self.calls.append({
            "host": host,
            "port": port,
            "kwargs": dict(kwargs),
        })
        return self.server


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


class _FakeRuntimeDriver:
    def __init__(self) -> None:
        self.spawned: set[str] = set()
        self.stopped: list[str] = []

    async def spawn(self, *, actor_id: str, control, profile=None) -> RuntimeSpawnResult:
        self.spawned.add(actor_id)
        now = profile.created_at if profile is not None else control.updated_at
        return RuntimeSpawnResult(
            attachment=Attachment(
                id=f"attachment:{actor_id}",
                actor_id=actor_id,
                kind=AttachmentKind.LOCAL_RUNTIME,
                endpoint={"driver": "fake"},
                capabilities=["reply"],
                created_at=now,
            ),
            presence=Presence(
                attachment_id=f"attachment:{actor_id}",
                actor_id=actor_id,
                gateway_id="gateway:test",
                status=PresenceStatus.ONLINE,
                leases=0,
                last_heartbeat_at=now,
            ),
        )

    async def stop(self, *, actor_id: str) -> None:
        self.spawned.discard(actor_id)
        self.stopped.append(actor_id)

    async def status(self, *, actor_id: str) -> dict | None:
        if actor_id not in self.spawned:
            return None
        return {"actor_id": actor_id, "driver": "fake"}


async def test_v2_gateway_application_and_control_plane_are_directly_usable(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "layers.db")))

    await gateway.start()
    try:
        assert isinstance(gateway.application, GatewayApplication)
        assert isinstance(gateway.control, GatewayControlPlane)
        assert isinstance(gateway.control.session_control, GatewaySessionControl)
        assert isinstance(gateway.control.binding_control, GatewayBindingControl)
        assert isinstance(gateway.control.delivery_control, GatewayDeliveryControl)
        assert isinstance(gateway.control.presence_control, GatewayPresenceControl)

        await gateway.application.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.application.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })

        binding = await gateway.control.binding_control.register_binding({
            "surface": "cli",
            "actor_id": "tyrant",
            "external_key": "cli:test",
            "thread_policy": "REUSE",
        })
        session = await gateway.control.session_control.open_session(
            surface="cli",
            actor_id="tyrant",
            external_key="cli:test",
        )
        result = await gateway.application.ingest({
            "sender_actor_id": "tyrant",
            "recipient_actor_ids": ["worker"],
            "payload": {"text": "hello"},
            "kind": "CHAT",
            "caller_actor_id": "tyrant",
        })
        await gateway.control.session_control.bind_session_thread(
            session.id,
            result["thread_id"],
        )

        attachment = await gateway.control.presence_control.register_attachment({
            "id": "worker-runtime",
            "actor_id": "worker",
            "kind": "LOCAL_RUNTIME",
            "endpoint": {"transport": "local"},
            "capabilities": ["reply"],
            "caller_actor_id": "tyrant",
        })
        presence = await gateway.control.presence_control.report_presence({
            "attachment_id": "worker-runtime",
            "actor_id": "worker",
            "gateway_id": "gateway:test",
            "status": "ONLINE",
            "leases": 1,
            "caller_actor_id": "tyrant",
        })

        sessions = await gateway.control.session_control.query_sessions(
            surface="cli",
            external_key="cli:test",
        )
        pending = await gateway.control.delivery_control.list_pending_deliveries("worker")
        attachments = await gateway.control.presence_control.query_attachments(actor_id="worker")
        fetched_presence = await gateway.control.presence_control.get_presence("worker-runtime")

        assert binding["actor_id"] == "tyrant"
        assert session.actor_id == "tyrant"
        assert sessions[0]["thread_id"] == result["thread_id"]
        assert pending[0]["envelope_id"] == result["envelope"]["id"]
        assert attachment["attachment"]["id"] == "worker-runtime"
        assert presence["presence"]["status"] == "ONLINE"
        assert [item["id"] for item in attachments] == ["worker-runtime"]
        assert fetched_presence is not None
        assert fetched_presence["gateway_id"] == "gateway:test"

        await gateway.control.delivery_control.ack_delivery(pending[0]["id"])
        acked = await gateway.control.delivery_control.query_deliveries(
            actor_id="worker",
            state="ACKED",
        )
        assert [item["envelope_id"] for item in acked] == [result["envelope"]["id"]]
    finally:
        await gateway.stop()


async def test_v2_gateway_server_registered_factory_worker_processes_queue(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "factory-worker.db")))
    worker = gateway.register_factory_worker(
        owner_actor_id="tyrant",
        executor_actor_id="factory",
        poll_interval=0.01,
        auto_reconcile=False,
    )

    await gateway.start()
    try:
        assert worker.running is True

        await gateway.application.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.application.create_actor({
            "id": "manager",
            "kind": "AGENT",
            "name": "Manager",
            "slug": "manager",
            "caller_actor_id": "tyrant",
        })
        await gateway.application.create_actor_profile({
            "id": "profile-1",
            "name": "worker-profile",
        })
        requested = await gateway.application.request_actor_provision({
            "requested_by_actor_id": "tyrant",
            "work_item_owner_actor_id": "tyrant",
            "caller_actor_id": "tyrant",
            "blueprint": {
                "name": "Worker Background",
                "parent_actor_id": "manager",
                "profile_id": "profile-1",
                "manager_actor_id": "tyrant",
                "metadata": {
                    "actor_id": "worker-bg",
                    "slug": "worker_bg",
                },
            },
        })

        for _ in range(50):
            work_item = await gateway.get_work_item(requested["work_item"]["id"])
            if work_item["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("factory worker did not complete provision work item in time")

        actor = await gateway.application.get_actor("worker-bg")
        control = await gateway.application.get_actor_control("worker-bg")

        assert actor["id"] == "worker-bg"
        assert control is not None
        assert control["manager_actor_id"] == "tyrant"
    finally:
        await gateway.stop()

    assert worker.running is False


async def test_v2_gateway_server_register_provisioning_worker_alias_processes_queue(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "provisioning-worker.db"))
    )
    worker = gateway.register_provisioning_worker(
        owner_actor_id="tyrant",
        executor_actor_id="factory",
        poll_interval=0.01,
        auto_reconcile=False,
    )

    await gateway.start()
    try:
        assert worker.running is True

        await gateway.application.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.application.create_actor({
            "id": "manager",
            "kind": "AGENT",
            "name": "Manager",
            "slug": "manager",
            "caller_actor_id": "tyrant",
        })
        requested = await gateway.application.request_actor_provision({
            "requested_by_actor_id": "tyrant",
            "work_item_owner_actor_id": "tyrant",
            "caller_actor_id": "tyrant",
            "blueprint": {
                "name": "Worker Provisioning Alias",
                "parent_actor_id": "manager",
                "manager_actor_id": "tyrant",
                "metadata": {
                    "actor_id": "worker-provision-alias",
                    "slug": "worker_provision_alias",
                },
            },
        })

        for _ in range(50):
            work_item = await gateway.get_work_item(requested["work_item"]["id"])
            if work_item["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("provisioning worker did not complete work item in time")

        actor = await gateway.application.get_actor("worker-provision-alias")
        assert actor["id"] == "worker-provision-alias"
    finally:
        await gateway.stop()

    assert worker.running is False


async def test_v2_gateway_server_registered_reconcile_worker_drives_runtime_state(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "reconcile-worker.db"))
    supervisor = RuntimeSupervisor(
        build_single_runtime_driver_registry(_FakeRuntimeDriver()),
        store=store,
    )
    reconciler = ActorReconciler(supervisor, store=store)
    api = SystemAPI(
        store=store,
        runtime_supervisor=supervisor,
        reconciler=reconciler,
    )
    gateway = GatewayServer(store=store, api=api)
    worker = gateway.register_reconcile_worker(
        poll_interval=0.01,
        limit=10,
    )

    await gateway.start()
    try:
        assert worker.running is True

        await gateway.application.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.application.create_actor({
            "id": "worker-r",
            "kind": "AGENT",
            "name": "Worker R",
            "slug": "worker_r",
            "caller_actor_id": "tyrant",
        })
        await gateway.application.create_actor_control({
            "id": "control-r",
            "actor_id": "worker-r",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })

        for _ in range(50):
            status = await gateway.get_runtime_status("worker-r")
            if status is not None:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("reconcile worker did not spawn runtime in time")

        assert status == {"actor_id": "worker-r", "driver": "fake"}
    finally:
        await gateway.stop()

    assert worker.running is False


async def test_v2_gateway_server_register_websocket_server_registers_managed_shell(
    tmp_path,
) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "websocket-server-shell.db"))
    )
    serve = _FakeWebSocketServeFactory()
    shell = gateway.register_websocket_server(
        host="127.0.0.1",
        port=0,
        serve_fn=serve,
        name="ws-shell",
    )

    assert gateway.manager.list_services() == ["ws-shell"]

    await gateway.start()
    try:
        assert shell.url() == "ws://127.0.0.1:9012"
        assert serve.calls == [{"host": "127.0.0.1", "port": 0, "kwargs": {}}]
    finally:
        await gateway.stop()

    assert serve.server.closed == 1
    assert serve.server.waited == 1
