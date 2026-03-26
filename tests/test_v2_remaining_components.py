from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import builtins
import pytest

import tyrant_core.gateway.binding_resolver as binding_resolver_module
from tyrant_core.gateway.binding_resolver import BindingMatch, BindingResolver
from tyrant_core.gateway.connectors.cli import CliConnector
from tyrant_core.gateway.delivery_engine import DeliveryEngine
from tyrant_core.management import (
    ActorManagementService,
    ActorRecoveryService,
    ActorReconcileWorker,
    ActorReconciler,
)
from tyrant_core.models import (
    Actor,
    ActorKind,
    ActorControl,
    Attachment,
    AttachmentKind,
    Binding,
    BindingThreadPolicy,
    Delivery,
    DeliveryState,
    DesiredState,
    Presence,
    PresenceStatus,
    RuntimeKind,
    ValidationError,
    WorkItem,
    WorkItemStatus,
)
from tyrant_core.provisioning import (
    AgentFactoryService,
    ProvisioningFactoryWorker,
    ProvisioningQueueProcessor,
    ProvisioningService,
    ProvisioningWorkItemHandler,
)
from tyrant_core.runtime.driver_registry import (
    RuntimeDriverRegistry,
    build_single_runtime_driver_registry,
)
from tyrant_core.runtime.actor_runtime import ActorRuntime
from tyrant_core.runtime.runtime_driver import RuntimeSpawnResult
from tyrant_core.runtime.runtime_supervisor import RuntimeSupervisor
from tyrant_core.runtime.file_tools import build_file_tools
from tyrant_core.store import SqliteStore


def _binding(
    *,
    binding_id: str = "binding-1",
    peer: str | None = "cli:test",
) -> Binding:
    return Binding(
        id=binding_id,
        surface="cli",
        account_id="acc-1",
        peer=peer,
        actor_id="actor-1",
        thread_policy=BindingThreadPolicy.REUSE,
        fixed_thread_id="thread-1",
        priority=3,
        metadata={"x": 1},
        created_at=datetime.now(timezone.utc),
    )


def _delivery(delivery_id: str = "delivery-1") -> Delivery:
    return Delivery(
        id=delivery_id,
        envelope_id="env-1",
        recipient_actor_id="actor-1",
        state=DeliveryState.PENDING,
        created_at=datetime.now(timezone.utc),
    )


class FakeBindingStore:
    def __init__(self) -> None:
        self.saved: list[Binding] = []
        self.resolved_binding: Binding | None = _binding()
        self.query_result = [_binding()]
        self.calls: list[tuple[str, dict]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def save_binding(self, binding: Binding) -> None:
        self.saved.append(binding)

    async def resolve_binding(self, **kwargs) -> Binding | None:
        self.calls.append(("resolve_binding", dict(kwargs)))
        return self.resolved_binding

    async def query_bindings(self, **kwargs) -> list[Binding]:
        self.calls.append(("query_bindings", dict(kwargs)))
        return list(self.query_result)


class FakeDeliveryStore:
    def __init__(self) -> None:
        self.leased = [_delivery()]
        self.acked: list[str] = []
        self.failed: list[tuple[str, dict | None]] = []
        self.query_calls: list[dict] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def lease_deliveries(self, **kwargs) -> list[Delivery]:
        self.query_calls.append(dict(kwargs))
        return list(self.leased)

    async def ack_delivery(self, delivery_id: str) -> None:
        self.acked.append(delivery_id)

    async def fail_delivery(self, delivery_id: str, error: dict | None = None) -> None:
        self.failed.append((delivery_id, error))

    async def query_deliveries(self, **kwargs) -> list[Delivery]:
        self.query_calls.append(dict(kwargs))
        return list(self.leased)


class FakeCliGateway:
    def __init__(self, thread_id: str | None) -> None:
        self.session = type("SessionRef", (), {"id": "session-1", "thread_id": thread_id})()
        self.open_calls: list[dict] = []
        self.ingest_calls: list[dict] = []
        self.bind_calls: list[tuple[str, str]] = []

    async def open_session(self, **kwargs):
        self.open_calls.append(dict(kwargs))
        return self.session

    async def ingest(self, spec: dict):
        self.ingest_calls.append(dict(spec))
        return {"thread_id": "thread-new", "envelope": {"id": "env-1"}}

    async def bind_session_thread(self, session_id: str, thread_id: str):
        self.bind_calls.append((session_id, thread_id))


class FakeRuntimeGateway:
    def __init__(self) -> None:
        self.leased = [{
            "delivery": {"id": "delivery-1", "envelope_id": "env-1"},
        }]
        self.envelope = {
            "id": "env-1",
            "thread_id": "thread-1",
            "priority": 2,
            "payload": {"text": "hello"},
        }
        self.calls: list[tuple[str, object]] = []

    async def lease_deliveries(self, **kwargs):
        self.calls.append(("lease_deliveries", dict(kwargs)))
        return list(self.leased)

    async def get_envelope(self, envelope_id: str):
        self.calls.append(("get_envelope", envelope_id))
        return dict(self.envelope)

    async def ingest(self, spec: dict):
        self.calls.append(("ingest", dict(spec)))
        return {"envelope": {"id": "reply-1"}}

    async def ack_delivery(self, delivery_id: str):
        self.calls.append(("ack_delivery", delivery_id))

    async def fail_delivery(self, delivery_id: str, error: dict | None = None):
        self.calls.append(("fail_delivery", {"delivery_id": delivery_id, "error": error}))


class FakeSpawnDriver:
    def __init__(
        self,
        *,
        label: str = "fake",
        attachment_kind: AttachmentKind = AttachmentKind.LOCAL_RUNTIME,
    ) -> None:
        self.label = label
        self.attachment_kind = attachment_kind
        self.spawn_calls: list[str] = []
        self.stop_calls: list[str] = []

    async def spawn(self, *, actor_id: str, control, profile=None) -> RuntimeSpawnResult:
        self.spawn_calls.append(actor_id)
        now = datetime.now(timezone.utc)
        return RuntimeSpawnResult(
            attachment=Attachment(
                id=f"attachment:{actor_id}",
                actor_id=actor_id,
                kind=self.attachment_kind,
                endpoint={"driver": self.label},
                capabilities=["reply"],
                created_at=now,
            ),
            presence=Presence(
                attachment_id=f"attachment:{actor_id}",
                actor_id=actor_id,
                gateway_id=f"gateway:{self.label}",
                status=PresenceStatus.ONLINE,
                leases=0,
                last_heartbeat_at=now,
            ),
        )

    async def stop(self, *, actor_id: str) -> None:
        self.stop_calls.append(actor_id)

    async def status(self, *, actor_id: str) -> dict | None:
        return {"actor_id": actor_id, "driver": self.label}


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def test_v2_binding_resolver_register_resolve_and_query(monkeypatch) -> None:
    store = FakeBindingStore()
    resolver = BindingResolver(store)
    monkeypatch.setattr(binding_resolver_module, "uuid4", lambda: "binding-new")

    created = await resolver.register(BindingMatch(
        actor_id="actor-1",
        surface="cli",
        external_key="cli:test",
        metadata={"x": 1},
    ))
    resolved = await resolver.resolve("cli", "cli:test", account_id="acc-1")
    queried = await resolver.query(surface="cli", actor_id="actor-1", limit=2)

    store.resolved_binding = _binding(binding_id="binding-2", peer=None)
    fallback_peer = await resolver.resolve("cli", "peer-fallback")
    store.resolved_binding = None
    missing = await resolver.resolve("cli", "missing")

    assert created.id == "binding-new"
    assert created.thread_policy == BindingThreadPolicy.REUSE
    assert store.saved[0].metadata == {"x": 1}
    assert resolved == BindingMatch(
        actor_id="actor-1",
        surface="cli",
        external_key="cli:test",
        binding_id="binding-1",
        account_id="acc-1",
        thread_policy="REUSE",
        fixed_thread_id="thread-1",
        priority=3,
        metadata={"x": 1},
    )
    assert queried == [resolved]
    assert fallback_peer is not None
    assert fallback_peer.external_key == "peer-fallback"
    assert missing is None


async def test_v2_delivery_engine_leases_acks_fails_and_queries() -> None:
    store = FakeDeliveryStore()
    engine = DeliveryEngine(store)

    leased = await engine.lease(
        actor_id="actor-1",
        consumer_id="consumer-1",
        limit=2,
        lease_seconds=15,
    )
    await engine.ack("delivery-1")
    await engine.fail("delivery-1", error={"message": "boom"})
    queried = await engine.query(actor_id="actor-1", state="PENDING", limit=3)

    assert leased[0].delivery.id == "delivery-1"
    assert leased[0].consumer_id == "consumer-1"
    assert store.query_calls[0] == {
        "recipient_actor_id": "actor-1",
        "lease_owner": "consumer-1",
        "limit": 2,
        "lease_seconds": 15,
    }
    assert store.acked == ["delivery-1"]
    assert store.failed == [("delivery-1", {"message": "boom"})]
    assert queried[0].id == "delivery-1"
    assert store.query_calls[1] == {
        "recipient_actor_id": "actor-1",
        "state": "PENDING",
        "limit": 3,
    }


async def test_v2_cli_connector_handles_bind_and_existing_thread() -> None:
    gateway_without_thread = FakeCliGateway(thread_id=None)
    connector = CliConnector(gateway_without_thread)

    assert await connector.start() is None
    result = await connector.send_text(
        sender_actor_id="tyrant",
        recipient_actor_id="worker",
        text="hello",
        external_key="cli:test",
    )
    assert await connector.stop() is None

    assert result["thread_id"] == "thread-new"
    assert gateway_without_thread.open_calls == [{
        "surface": "cli",
        "actor_id": "tyrant",
        "external_key": "cli:test",
        "thread_id": None,
    }]
    assert gateway_without_thread.ingest_calls == [{
        "sender_actor_id": "tyrant",
        "recipient_actor_ids": ["worker"],
        "thread_id": None,
        "payload": {"text": "hello"},
        "kind": "CHAT",
        "subject": "cli:test",
        "caller_actor_id": "tyrant",
    }]
    assert gateway_without_thread.bind_calls == [("session-1", "thread-new")]

    gateway_with_thread = FakeCliGateway(thread_id="thread-existing")
    connector_with_thread = CliConnector(gateway_with_thread)
    await connector_with_thread.send_text(
        sender_actor_id="tyrant",
        recipient_actor_id="worker",
        text="hello",
        external_key="cli:test",
        thread_id="thread-explicit",
    )

    assert gateway_with_thread.ingest_calls[0]["thread_id"] == "thread-explicit"
    assert gateway_with_thread.bind_calls == []


async def test_v2_actor_runtime_handles_sync_async_and_failure_paths() -> None:
    gateway = FakeRuntimeGateway()
    runtime = ActorRuntime(gateway)

    processed = await runtime.consume_once(
        actor_id="actor-1",
        consumer_id="consumer-1",
        handler=lambda envelope, delivery: {
            "recipient_actor_ids": ["actor-2"],
            "payload": {"text": envelope["payload"]["text"].upper()},
        },
    )

    async def async_handler(envelope: dict, delivery: dict):
        return None

    processed_async = await runtime.consume_once(
        actor_id="actor-1",
        consumer_id="consumer-2",
        handler=async_handler,
    )

    async def failing_handler(envelope: dict, delivery: dict):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.consume_once(
            actor_id="actor-1",
            consumer_id="consumer-3",
            handler=failing_handler,
        )

    assert processed == 1
    assert processed_async == 1
    assert ("ack_delivery", "delivery-1") in gateway.calls
    assert (
        "ingest",
        {
            "sender_actor_id": "actor-1",
            "recipient_actor_ids": ["actor-2"],
            "thread_id": "thread-1",
            "kind": "CHAT",
            "payload": {"text": "HELLO"},
            "priority": 2,
            "correlation_id": "env-1",
            "bypass_policy": True,
        },
    ) in gateway.calls
    assert (
        "fail_delivery",
        {
            "delivery_id": "delivery-1",
            "error": {"error": "boom", "consumer_id": "consumer-3"},
        },
    ) in gateway.calls


async def test_v2_file_tools_cover_empty_and_permission_paths(monkeypatch, tmp_path) -> None:
    tools = {tool.__name__: tool for tool in build_file_tools()}
    file_read = tools["file_read"]
    file_grep = tools["file_grep"]

    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")

    original_open = builtins.open

    def permission_open(path, *args, **kwargs):
        if str(path).endswith("forbidden.txt"):
            raise PermissionError
        if str(path).endswith("binary.txt"):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", permission_open)

    empty_result = await file_read(str(empty))
    denied_read = await file_read(str(tmp_path / "forbidden.txt"))
    denied_grep = await file_grep(str(tmp_path / "forbidden.txt"), "x")
    binary_grep = await file_grep(str(tmp_path / "binary.txt"), "x")

    assert empty_result == {"content": "", "total_lines": 0}
    assert denied_read == {"error": f"无权限读取: {tmp_path / 'forbidden.txt'}"}
    assert denied_grep == {"error": f"无权限读取: {tmp_path / 'forbidden.txt'}"}
    assert binary_grep == {"error": f"文件非文本格式: {tmp_path / 'binary.txt'}"}


async def test_v2_management_service_manages_profiles_and_controls(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "management-service.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="worker",
                kind=ActorKind.AGENT,
                name="Worker",
                slug="worker",
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="manager-a",
                kind=ActorKind.AGENT,
                name="ManagerA",
                slug="manager-a",
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="manager-b",
                kind=ActorKind.AGENT,
                name="ManagerB",
                slug="manager-b",
                created_at=now,
            )
        )

    service = ActorManagementService(store=store)
    profile = await service.create_profile({
        "id": "profile-1",
        "name": "worker-default",
        "instruction": "do work",
        "toolsets": ["terminal"],
    })
    control = await service.create_control({
        "id": "control-1",
        "actor_id": "worker",
        "manager_actor_id": "manager-a",
        "profile_id": profile.id,
    })
    reassigned = await service.assign_manager("worker", "manager-b")
    paused = await service.set_desired_state("worker", "PAUSED")
    profiled = await service.set_profile("worker", "profile-1")
    queried = await service.query_controls(manager_actor_id="manager-b", desired_state="PAUSED")

    assert profile.name == "worker-default"
    assert control.actor_id == "worker"
    assert reassigned.manager_actor_id == "manager-b"
    assert paused.desired_state == DesiredState.PAUSED
    assert profiled.profile_id == "profile-1"
    assert [item.actor_id for item in queried] == ["worker"]
    await store.close()


async def test_v2_provisioning_service_submits_and_executes_template_request(
    tmp_path,
) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "provisioning-service.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="tyrant",
                kind=ActorKind.TYRANT,
                name="Tyrant",
                slug="tyrant",
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="manager",
                kind=ActorKind.AGENT,
                name="Manager",
                slug="manager",
                created_at=now,
            )
        )

    management = ActorManagementService(store=store)
    await management.create_profile({
        "id": "profile-1",
        "name": "research-worker",
    })

    provisioning = ProvisioningService(store=store)
    request = await provisioning.submit_request({
        "id": "request-1",
        "requested_by_actor_id": "tyrant",
        "manager_actor_id": "tyrant",
        "profile_id": "profile-1",
        "blueprint": {
            "name": "Worker One",
            "parent_actor_id": "manager",
            "profile_id": "profile-1",
            "manager_actor_id": "tyrant",
            "metadata": {
                "actor_id": "worker-1",
                "slug": "worker_1",
            },
        },
    })
    record = await provisioning.execute_request("request-1", executor_actor_id="factory")

    async with store.transaction():
        actor = await store.get_actor("worker-1")
        control = await store.get_actor_control("worker-1")
        edges = await store.list_org_edges(src_actor_id="manager", kind="REPORTS_TO")
        saved_request = await store.get_provision_request("request-1")
        records = await store.query_provision_records(request_id="request-1")

    assert request.status.name == "PENDING"
    assert record.produced_actor_id == "worker-1"
    assert actor is not None and actor.slug == "worker_1"
    assert control is not None and control.manager_actor_id == "tyrant"
    assert [edge.dst_actor_id for edge in edges] == ["worker-1"]
    assert saved_request is not None
    assert saved_request.status.name == "SUCCEEDED"
    assert [item.id for item in records] == [record.id]
    await store.close()


async def test_v2_runtime_supervisor_and_reconciler_drive_runtime_state(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "runtime-reconcile.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="worker",
                kind=ActorKind.AGENT,
                name="Worker",
                slug="worker",
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="manager",
                kind=ActorKind.AGENT,
                name="Manager",
                slug="manager",
                created_at=now,
            )
        )
        await store.save_actor_control(
            ActorControl(
                id="control-1",
                actor_id="worker",
                manager_actor_id="manager",
                desired_state=DesiredState.ACTIVE,
                updated_at=now,
            )
        )

    driver = FakeSpawnDriver()
    supervisor = RuntimeSupervisor(
        build_single_runtime_driver_registry(driver),
        store=store,
    )
    reconciler = ActorReconciler(supervisor, store=store)

    changed_activate = await reconciler.reconcile_actor("worker")
    status = await supervisor.get_runtime_status("worker")

    async with store.transaction():
        attachments = await store.query_attachments(actor_id="worker")
        presence = await store.get_presence("attachment:worker")
        await store.update_actor_control(
            "worker",
            desired_state=DesiredState.PAUSED,
            updated_at=now,
        )

    changed_pause = await reconciler.reconcile_actor("worker")

    async with store.transaction():
        stopped_attachments = await store.query_attachments(actor_id="worker")
        stopped_presence = await store.get_presence("attachment:worker")

    assert changed_activate is True
    assert driver.spawn_calls == ["worker"]
    assert status == {"actor_id": "worker", "driver": "fake"}
    assert len(attachments) == 1
    assert attachments[0].detached_at is None
    assert presence is not None
    assert presence.status == PresenceStatus.ONLINE

    assert changed_pause is True
    assert driver.stop_calls == ["worker"]
    assert len(stopped_attachments) == 1
    assert stopped_attachments[0].detached_at is not None
    assert stopped_presence is not None
    assert stopped_presence.status == PresenceStatus.OFFLINE
    await store.close()


def test_v2_runtime_driver_registry_registers_and_lists_runtime_kinds() -> None:
    local = FakeSpawnDriver(label="local")
    remote = FakeSpawnDriver(label="remote")
    registry = RuntimeDriverRegistry()

    registry.register(RuntimeKind.LOCAL, local)
    registry.register("REMOTE", remote)

    assert registry.get(RuntimeKind.LOCAL) is local
    assert registry.get("REMOTE") is remote
    assert registry.list_runtime_kinds() == ["LOCAL", "REMOTE"]


async def test_v2_runtime_supervisor_routes_by_control_runtime_kind(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "runtime-routing.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="worker-remote",
                kind=ActorKind.AGENT,
                name="Worker Remote",
                slug="worker_remote",
                created_at=now,
            )
        )
        await store.save_actor_control(
            ActorControl(
                id="control-remote",
                actor_id="worker-remote",
                manager_actor_id="manager",
                runtime_kind=RuntimeKind.REMOTE,
                desired_state=DesiredState.ACTIVE,
                updated_at=now,
            )
        )

    local_driver = FakeSpawnDriver(label="local")
    remote_driver = FakeSpawnDriver(label="remote")
    registry = RuntimeDriverRegistry()
    registry.register(RuntimeKind.LOCAL, local_driver)
    registry.register(RuntimeKind.REMOTE, remote_driver)
    supervisor = RuntimeSupervisor(registry, store=store)

    result = await supervisor.spawn_actor_runtime("worker-remote")
    status = await supervisor.get_runtime_status("worker-remote")

    assert local_driver.spawn_calls == []
    assert remote_driver.spawn_calls == ["worker-remote"]
    assert result["attachment"]["endpoint"]["driver"] == "remote"
    assert status == {"actor_id": "worker-remote", "driver": "remote"}
    await store.close()


async def test_v2_runtime_supervisor_rejects_unregistered_runtime_kind(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "runtime-missing-driver.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="worker-terminal",
                kind=ActorKind.AGENT,
                name="Worker Terminal",
                slug="worker_terminal",
                created_at=now,
            )
        )
        await store.save_actor_control(
            ActorControl(
                id="control-terminal",
                actor_id="worker-terminal",
                manager_actor_id="manager",
                runtime_kind=RuntimeKind.TERMINAL,
                desired_state=DesiredState.ACTIVE,
                updated_at=now,
            )
        )

    supervisor = RuntimeSupervisor(RuntimeDriverRegistry(), store=store)

    with pytest.raises(ValidationError, match="runtime driver is not registered: TERMINAL"):
        await supervisor.spawn_actor_runtime("worker-terminal")

    await store.close()


async def test_v2_provisioning_work_item_handler_executes_and_updates_work_item(
    tmp_path,
) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "provisioning-work-item.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="tyrant",
                kind=ActorKind.TYRANT,
                name="Tyrant",
                slug="tyrant",
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="manager",
                kind=ActorKind.AGENT,
                name="Manager",
                slug="manager",
                created_at=now,
            )
        )

    management = ActorManagementService(store=store)
    await management.create_profile({"id": "profile-1", "name": "worker"})
    provisioning = ProvisioningService(store=store)
    requested = await provisioning.submit_request({
        "id": "request-1",
        "requested_by_actor_id": "tyrant",
        "manager_actor_id": "tyrant",
        "profile_id": "profile-1",
        "blueprint": {
            "name": "Worker Three",
            "parent_actor_id": "manager",
            "profile_id": "profile-1",
            "manager_actor_id": "tyrant",
            "metadata": {
                "actor_id": "worker-3",
                "slug": "worker_3",
            },
        },
    })
    async with store.transaction():
        await store.save_work_item(
            WorkItem(
                id="work-provision",
                kind="PROVISION_ACTOR",
                creator_actor_id="tyrant",
                owner_actor_id="tyrant",
                thread_id="thread-1",
                status=WorkItemStatus.PENDING,
                result={"provision_request_id": requested.id},
                created_at=now,
                updated_at=now,
            )
        )

    supervisor = RuntimeSupervisor(
        build_single_runtime_driver_registry(FakeSpawnDriver()),
        store=store,
    )
    reconciler = ActorReconciler(supervisor, store=store)
    handler = ProvisioningWorkItemHandler(
        provisioning,
        store=store,
        reconciler=reconciler,
    )
    result = await handler.process(
        "work-provision",
        executor_actor_id="factory",
    )

    async with store.transaction():
        work_item = await store.get_work_item("work-provision")
        presence = await store.get_presence("attachment:worker-3")

    assert result["provision_record"]["produced_actor_id"] == "worker-3"
    assert result["reconciled"] is True
    assert work_item is not None
    assert work_item.status == WorkItemStatus.COMPLETED
    assert work_item.result["produced_actor_id"] == "worker-3"
    assert presence is not None
    assert presence.status == PresenceStatus.ONLINE
    await store.close()


async def test_v2_agent_factory_service_claims_next_provision_work_item_and_skips_other_kinds(
    tmp_path,
) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "agent-factory-service.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="tyrant",
                kind=ActorKind.TYRANT,
                name="Tyrant",
                slug="tyrant",
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="manager",
                kind=ActorKind.AGENT,
                name="Manager",
                slug="manager",
                created_at=now,
            )
        )

    management = ActorManagementService(store=store)
    await management.create_profile({"id": "profile-1", "name": "worker"})
    provisioning = ProvisioningService(store=store)
    requested = await provisioning.submit_request({
        "id": "request-2",
        "requested_by_actor_id": "tyrant",
        "manager_actor_id": "tyrant",
        "profile_id": "profile-1",
        "blueprint": {
            "name": "Worker Four",
            "parent_actor_id": "manager",
            "profile_id": "profile-1",
            "manager_actor_id": "tyrant",
            "metadata": {
                "actor_id": "worker-4",
                "slug": "worker_4",
            },
        },
    })
    async with store.transaction():
        await store.save_work_item(
            WorkItem(
                id="work-provision",
                kind="PROVISION_ACTOR",
                creator_actor_id="tyrant",
                owner_actor_id="tyrant",
                thread_id="thread-1",
                status=WorkItemStatus.PENDING,
                result={"provision_request_id": requested.id},
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_work_item(
            WorkItem(
                id="work-task",
                kind="TASK",
                creator_actor_id="tyrant",
                owner_actor_id="tyrant",
                thread_id="thread-1",
                status=WorkItemStatus.PENDING,
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
        )

    supervisor = RuntimeSupervisor(
        build_single_runtime_driver_registry(FakeSpawnDriver()),
        store=store,
    )
    reconciler = ActorReconciler(supervisor, store=store)
    handler = ProvisioningWorkItemHandler(
        provisioning,
        store=store,
        reconciler=reconciler,
    )
    factory = AgentFactoryService(store=store, handler=handler)

    result = await factory.process_next(
        owner_actor_id="tyrant",
        executor_actor_id="factory",
        scan_limit=5,
    )

    async with store.transaction():
        task_item = await store.get_work_item("work-task")
        provision_item = await store.get_work_item("work-provision")
        presence = await store.get_presence("attachment:worker-4")

    assert result is not None
    assert result["provision_record"]["produced_actor_id"] == "worker-4"
    assert result["reconciled"] is True
    assert task_item is not None
    assert task_item.status == WorkItemStatus.PENDING
    assert provision_item is not None
    assert provision_item.status == WorkItemStatus.COMPLETED
    assert provision_item.result["produced_actor_id"] == "worker-4"
    assert presence is not None
    assert presence.status == PresenceStatus.ONLINE
    await store.close()


async def test_v2_provisioning_queue_processor_claims_next_provision_work_item(
    tmp_path,
) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "provisioning-queue-processor.db"))
    now = datetime.now(timezone.utc)

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="tyrant",
                kind=ActorKind.TYRANT,
                name="Tyrant",
                slug="tyrant",
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="manager",
                kind=ActorKind.AGENT,
                name="Manager",
                slug="manager",
                created_at=now,
            )
        )

    management = ActorManagementService(store=store)
    await management.create_profile({"id": "profile-queue", "name": "worker"})
    provisioning = ProvisioningService(store=store)
    requested = await provisioning.submit_request({
        "id": "request-queue",
        "requested_by_actor_id": "tyrant",
        "manager_actor_id": "tyrant",
        "profile_id": "profile-queue",
        "blueprint": {
            "name": "Worker Queue",
            "parent_actor_id": "manager",
            "profile_id": "profile-queue",
            "manager_actor_id": "tyrant",
            "metadata": {
                "actor_id": "worker-queue",
                "slug": "worker_queue",
            },
        },
    })
    async with store.transaction():
        await store.save_work_item(
            WorkItem(
                id="work-provision-queue",
                kind="PROVISION_ACTOR",
                creator_actor_id="tyrant",
                owner_actor_id="tyrant",
                thread_id="thread-queue",
                status=WorkItemStatus.PENDING,
                result={"provision_request_id": requested.id},
                created_at=now,
                updated_at=now,
            )
        )

    supervisor = RuntimeSupervisor(
        build_single_runtime_driver_registry(FakeSpawnDriver()),
        store=store,
    )
    reconciler = ActorReconciler(supervisor, store=store)
    handler = ProvisioningWorkItemHandler(
        provisioning,
        store=store,
        reconciler=reconciler,
    )
    processor = ProvisioningQueueProcessor(store=store, handler=handler)

    result = await processor.process_next(
        owner_actor_id="tyrant",
        executor_actor_id="factory",
        scan_limit=5,
    )

    async with store.transaction():
        provision_item = await store.get_work_item("work-provision-queue")
        presence = await store.get_presence("attachment:worker-queue")

    assert result is not None
    assert result["provision_record"]["produced_actor_id"] == "worker-queue"
    assert provision_item is not None
    assert provision_item.status == WorkItemStatus.COMPLETED
    assert presence is not None
    assert presence.status == PresenceStatus.ONLINE
    await store.close()


async def test_v2_provisioning_factory_worker_tick_and_background_loop() -> None:
    processed = asyncio.Event()

    class FakeAgentFactory:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def process_next(self, **kwargs) -> dict | None:
            self.calls.append(dict(kwargs))
            if len(self.calls) == 1:
                return None
            processed.set()
            return {"work_item": {"id": "work-1"}}

    factory = FakeAgentFactory()
    worker = ProvisioningFactoryWorker(
        factory,  # type: ignore[arg-type]
        owner_actor_id="factory-owner",
        executor_actor_id="factory-executor",
        poll_interval=0.01,
        auto_reconcile=False,
        scan_limit=5,
    )

    assert await worker.tick() is None
    assert worker.running is False

    await worker.start()
    await asyncio.wait_for(processed.wait(), timeout=1)
    assert worker.running is True
    await worker.stop()

    assert worker.running is False
    assert worker.last_error is None
    assert factory.calls[0] == {
        "owner_actor_id": "factory-owner",
        "executor_actor_id": "factory-executor",
        "auto_reconcile": False,
        "scan_limit": 5,
    }
    assert len(factory.calls) >= 2


async def test_v2_actor_reconcile_worker_tick_and_background_loop() -> None:
    reconciled = asyncio.Event()

    class FakeReconciler:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def reconcile_all(self, *, limit: int = 1000) -> int:
            self.calls.append(limit)
            if len(self.calls) == 1:
                return 0
            reconciled.set()
            return 1

    reconciler = FakeReconciler()
    worker = ActorReconcileWorker(
        reconciler,  # type: ignore[arg-type]
        poll_interval=0.01,
        limit=7,
        name="reconcile-test",
    )

    assert await worker.tick() == 0
    assert worker.running is False

    await worker.start()
    await asyncio.wait_for(reconciled.wait(), timeout=1)
    assert worker.running is True
    await worker.stop()

    assert worker.running is False
    assert worker.last_error is None
    assert reconciler.calls[0] == 7
    assert len(reconciler.calls) >= 2


async def test_v2_actor_recovery_service_recovers_and_records_failures() -> None:
    class FakeControl:
        def __init__(self, actor_id: str) -> None:
            self.actor_id = actor_id

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def query_actor_controls(self, *, limit: int = 1000):
            assert limit == 5
            return [
                FakeControl("actor-1"),
                FakeControl("actor-2"),
                FakeControl("actor-3"),
            ]

    class FakeReconciler:
        def __init__(self) -> None:
            self.store = FakeStore()
            self.calls: list[str] = []

        async def reconcile_actor(self, actor_id: str) -> bool:
            self.calls.append(actor_id)
            if actor_id == "actor-2":
                raise RuntimeError("boom")
            return actor_id == "actor-1"

    reconciler = FakeReconciler()
    service = ActorRecoveryService(
        reconciler,  # type: ignore[arg-type]
        limit=5,
    )

    await service.start()
    await service.stop()

    assert reconciler.calls == ["actor-1", "actor-2", "actor-3"]
    assert service.recovered_count == 1
    assert service.last_error == "boom"
    assert service.failures == [{
        "actor_id": "actor-2",
        "error": "boom",
    }]
