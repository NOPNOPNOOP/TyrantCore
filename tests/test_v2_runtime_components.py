from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone

import pytest

from tyrant_core.gateway.connectors.cli import CliConnector
from tyrant_core.models import ActorControl, ActorProfile, RuntimeKind, ValidationError
from tyrant_core.projections import DeliveryProjection, SessionProjection
from tyrant_core.runtime import (
    ActorRuntime,
    CapabilityRegistry,
    ExternalRuntimeDriver,
    ExecutionManager,
    LocalRuntimeDriver,
    LocalRuntimeBehaviorRegistry,
    RemoteRuntimeDriver,
    TerminalRuntimeDriver,
    TraceEvent,
    TraceRecorder,
    build_embedded_runtime_gateway,
    install_builtin_local_runtime_behaviors,
    install_local_runtime_behavior_pack,
    build_local_runtime_gateway,
)


class FakeModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        assert mode == "json"
        return dict(self.payload)


class FakeStore:
    def __init__(self) -> None:
        self.delivery_queries: list[dict] = []
        self.session_queries: list[dict] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def query_deliveries(self, **kwargs) -> list[FakeModel]:
        self.delivery_queries.append(dict(kwargs))
        return [
            FakeModel({"id": "delivery-1", "state": kwargs["state"]}),
            FakeModel({"id": "delivery-2", "state": kwargs["state"]}),
        ]

    async def query_sessions(self, **kwargs) -> list[FakeModel]:
        self.session_queries.append(dict(kwargs))
        return [FakeModel({"id": "session-1", "surface": kwargs["surface"]})]


class FakeTerminalRuntimeGateway:
    def __init__(self, actor: dict | None = None) -> None:
        self.actor = actor or {
            "id": "terminal-worker",
            "metadata": {},
        }

    async def get_actor(self, actor_id: str) -> dict:
        assert actor_id == self.actor["id"]
        return dict(self.actor)


class FakeTerminalSession:
    def __init__(self, session_id: str, *, pid: int = 321) -> None:
        self.session_id = session_id
        self.pid = pid
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


class FakeTerminalSessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, FakeTerminalSession] = {}
        self.create_calls: list[dict] = []
        self.closed_sessions: list[str] = []
        self._counter = 0

    async def create_session(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        owner_agent_id: str = "",
    ) -> str:
        self._counter += 1
        session_id = f"session-{self._counter}"
        self.create_calls.append({
            "command": command,
            "cwd": cwd,
            "env": env,
            "owner_agent_id": owner_agent_id,
        })
        self.sessions[session_id] = FakeTerminalSession(session_id)
        return session_id

    def get_session(self, session_id: str) -> FakeTerminalSession | None:
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        self.closed_sessions.append(session_id)
        session = self.sessions.pop(session_id, None)
        if session is not None:
            session.alive = False


class FakeTerminalAdapter:
    instances: list["FakeTerminalAdapter"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.attachment_id = kwargs["attachment_id"]
        self.run_started = asyncio.Event()
        self.cancelled = False
        self.__class__.instances.append(self)

    async def run(self) -> None:
        self.run_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_v2_capability_registry_returns_copies() -> None:
    registry = CapabilityRegistry()

    registry.register("attachment-1", ["stdout", "stdin"])
    values = registry.get("attachment-1")
    values.append("mutated")

    assert registry.get("attachment-1") == ["stdout", "stdin"]
    assert registry.get("missing") == []


def test_v2_execution_manager_tracks_running_execution() -> None:
    manager = ExecutionManager()

    execution = manager.start(actor_id="actor-1", thread_id="thread-1")

    assert execution.actor_id == "actor-1"
    assert execution.thread_id == "thread-1"
    assert execution.status == "RUNNING"
    assert execution.started_at.tzinfo == timezone.utc
    assert manager._executions[execution.id] is execution


def test_v2_trace_recorder_snapshot_is_isolated() -> None:
    recorder = TraceRecorder()
    event = TraceEvent(
        kind="message.sent",
        actor_id="actor-1",
        payload={"text": "hello"},
    )

    recorder.record(event)
    snapshot = recorder.snapshot()
    snapshot.append(TraceEvent(kind="ignored", actor_id="actor-2"))

    assert recorder.snapshot() == [event]
    assert recorder.snapshot()[0].created_at.tzinfo == timezone.utc


def _build_control(actor_id: str = "actor-1") -> ActorControl:
    return ActorControl(
        id=f"control:{actor_id}",
        actor_id=actor_id,
        manager_actor_id="tyrant",
        runtime_kind=RuntimeKind.LOCAL,
        updated_at=datetime.now(timezone.utc),
    )


def _build_terminal_profile() -> ActorProfile:
    return ActorProfile(
        id="profile-terminal",
        name="terminal-profile",
        runtime_kind=RuntimeKind.TERMINAL,
        bootstrap_policy={
            "terminal_runtime": {
                "command": "bash",
                "cwd": "/profile",
                "aggregation_ms": 120,
            }
        },
        created_at=datetime.now(timezone.utc),
    )


async def test_v2_local_runtime_registry_starts_empty_until_installed() -> None:
    registry = LocalRuntimeBehaviorRegistry()

    assert registry.list_behaviors() == []

    with pytest.raises(ValidationError, match="unsupported local_runtime behavior: echo"):
        registry.build_handler(
            actor={
                "id": "worker",
                "metadata": {"local_runtime": {"behavior": "echo"}},
            },
            control=_build_control("worker"),
            profile=None,
        )


def test_v2_install_builtin_local_runtime_behaviors_registers_builtin_factories() -> None:
    registry = LocalRuntimeBehaviorRegistry()

    install_builtin_local_runtime_behaviors(registry)

    assert registry.list_behaviors() == ["echo", "noop", "template_reply"]


async def test_v2_local_runtime_behavior_pack_can_extend_and_override() -> None:
    registry = LocalRuntimeBehaviorRegistry()
    install_builtin_local_runtime_behaviors(registry)

    def installer(target: LocalRuntimeBehaviorRegistry) -> None:
        def reverse_factory(actor, control, profile, spec):
            del actor, control, profile, spec

            async def handler(envelope, delivery):
                del delivery
                return {
                    "recipient_actor_ids": [envelope["sender_actor_id"]],
                    "payload": {
                        "text": str(envelope["payload"]["text"])[::-1],
                    },
                }

            return handler

        def override_echo_factory(actor, control, profile, spec):
            del actor, control, profile, spec

            async def handler(envelope, delivery):
                del envelope, delivery
                return {
                    "recipient_actor_ids": ["tyrant"],
                    "payload": {"text": "override"},
                }

            return handler

        target.register("reverse", reverse_factory)
        target.register("echo", override_echo_factory)

    install_local_runtime_behavior_pack(registry, installer)

    reverse_handler = registry.build_handler(
        actor={
            "id": "worker",
            "metadata": {"local_runtime": {"behavior": "reverse"}},
        },
        control=_build_control("worker"),
        profile=None,
    )
    echo_handler = registry.build_handler(
        actor={
            "id": "worker",
            "metadata": {"local_runtime": {"behavior": "echo"}},
        },
        control=_build_control("worker"),
        profile=None,
    )

    assert reverse_handler is not None
    assert echo_handler is not None
    assert registry.list_behaviors() == ["echo", "noop", "reverse", "template_reply"]
    assert await reverse_handler(
        {"sender_actor_id": "tyrant", "payload": {"text": "stressed"}},
        {},
    ) == {
        "recipient_actor_ids": ["tyrant"],
        "payload": {"text": "desserts"},
    }
    assert await echo_handler(
        {"sender_actor_id": "sender", "payload": {"text": "ignored"}},
        {},
    ) == {
        "recipient_actor_ids": ["tyrant"],
        "payload": {"text": "override"},
    }


async def test_v2_terminal_runtime_driver_status_reports_none_when_not_running() -> None:
    driver = TerminalRuntimeDriver(
        gateway=FakeTerminalRuntimeGateway(),
        session_manager=FakeTerminalSessionManager(),
        adapter_factory=FakeTerminalAdapter,
    )

    status = await driver.status(actor_id="missing")

    assert status is None


async def test_v2_remote_runtime_driver_merges_profile_metadata_bootstrap_and_control() -> None:
    driver = RemoteRuntimeDriver(
        gateway=FakeTerminalRuntimeGateway({
            "id": "remote-worker",
            "metadata": {
                "remote_runtime": {
                    "transport": "websocket",
                    "gateway_id": "gateway:metadata-remote",
                },
                "bootstrap": {
                    "remote_runtime": {
                        "endpoint": {
                            "headers": {"X-Boot": "1"},
                        },
                        "status": "DEGRADED",
                    }
                },
            },
        }),
    )
    profile = ActorProfile(
        id="profile-remote",
        name="remote-profile",
        runtime_kind=RuntimeKind.REMOTE,
        bootstrap_policy={
            "remote_runtime": {
                "endpoint": {
                    "url": "wss://profile.example/ws",
                },
                "capabilities": ["events"],
            }
        },
        created_at=datetime.now(timezone.utc),
    )
    control = ActorControl(
        id="control-remote",
        actor_id="remote-worker",
        manager_actor_id="manager",
        runtime_kind=RuntimeKind.REMOTE,
        placement={
            "remote_runtime": {
                "endpoint": {
                    "url": "wss://control.example/ws",
                    "topic": "updates",
                },
                "capabilities": ["events", "deliver"],
            }
        },
        updated_at=datetime.now(timezone.utc),
    )

    result = await driver.spawn(
        actor_id="remote-worker",
        control=control,
        profile=profile,
    )
    status = await driver.status(actor_id="remote-worker")

    assert result.attachment.kind == "CONNECTOR"
    assert result.attachment.endpoint == {
        "url": "wss://control.example/ws",
        "headers": {"X-Boot": "1"},
        "transport": "websocket",
        "topic": "updates",
        "driver": "remote-runtime",
    }
    assert result.attachment.capabilities == ["events", "deliver"]
    assert result.presence is not None
    assert result.presence.gateway_id == "gateway:metadata-remote"
    assert result.presence.status == "DEGRADED"
    assert status == {
        "actor_id": "remote-worker",
        "driver": "remote-runtime",
        "endpoint": {
            "url": "wss://control.example/ws",
            "headers": {"X-Boot": "1"},
            "transport": "websocket",
            "topic": "updates",
            "driver": "remote-runtime",
        },
        "capabilities": ["events", "deliver"],
        "gateway_id": "gateway:metadata-remote",
        "presence_status": "DEGRADED",
        "running": True,
    }

    await driver.stop(actor_id="remote-worker")

    assert await driver.status(actor_id="remote-worker") is None


async def test_v2_external_runtime_driver_accepts_string_endpoint_and_validates_capabilities() -> None:
    driver = ExternalRuntimeDriver(
        gateway=FakeTerminalRuntimeGateway({
            "id": "external-worker",
            "metadata": {
                "external_runtime": "https://profile.example/hook",
            },
        }),
    )
    control = ActorControl(
        id="control-external",
        actor_id="external-worker",
        manager_actor_id="manager",
        runtime_kind=RuntimeKind.EXTERNAL,
        placement={
            "external_runtime": {
                "method": "POST",
                "capabilities": ["ingest"],
            }
        },
        updated_at=datetime.now(timezone.utc),
    )

    result = await driver.spawn(
        actor_id="external-worker",
        control=control,
        profile=None,
    )

    assert result.attachment.kind == "WEBHOOK"
    assert result.attachment.endpoint == {
        "url": "https://profile.example/hook",
        "method": "POST",
        "driver": "external-runtime",
    }
    assert result.attachment.capabilities == ["ingest"]

    bad_control = control.model_copy(
        update={
            "placement": {
                "external_runtime": {
                    "endpoint": {"url": "https://broken.example/hook"},
                    "capabilities": "ingest",
                }
            }
        }
    )
    bad_driver = ExternalRuntimeDriver(
        gateway=FakeTerminalRuntimeGateway({"id": "external-worker", "metadata": {}}),
    )
    with pytest.raises(
        ValidationError,
        match="external runtime capabilities must be a list",
    ):
        await bad_driver.spawn(
            actor_id="external-worker",
            control=bad_control,
            profile=None,
        )


async def test_v2_terminal_runtime_driver_merges_profile_bootstrap_actor_metadata_and_control_placement() -> None:
    FakeTerminalAdapter.instances.clear()
    session_manager = FakeTerminalSessionManager()
    driver = TerminalRuntimeDriver(
        gateway=FakeTerminalRuntimeGateway({
            "id": "terminal-worker",
            "metadata": {
                "terminal_runtime": {
                    "cwd": "/actor",
                    "controller_actor_id": "metadata-controller",
                },
                "bootstrap": {
                    "terminal_runtime": {
                        "env": {"BOOT": "1"},
                    }
                },
            },
        }),
        session_manager=session_manager,
        adapter_factory=FakeTerminalAdapter,
        aggregation_ms=500,
    )
    control = ActorControl(
        id="control-terminal",
        actor_id="terminal-worker",
        manager_actor_id="manager",
        runtime_kind=RuntimeKind.TERMINAL,
        placement={
            "terminal_runtime": {
                "command": "bash -lc 'echo ok'",
                "controller_actor_id": "control-controller",
                "thread_id": "thread-1",
            }
        },
        updated_at=datetime.now(timezone.utc),
    )

    result = await driver.spawn(
        actor_id="terminal-worker",
        control=control,
        profile=_build_terminal_profile(),
    )
    adapter = FakeTerminalAdapter.instances[0]
    await asyncio.wait_for(adapter.run_started.wait(), timeout=1.0)
    status = await driver.status(actor_id="terminal-worker")

    assert session_manager.create_calls == [{
        "command": "bash -lc 'echo ok'",
        "cwd": "/actor",
        "env": {"BOOT": "1"},
        "owner_agent_id": "terminal-worker",
    }]
    assert result.attachment.kind == "TERMINAL"
    assert result.attachment.endpoint == {
        "driver": "terminal-runtime",
        "session_id": "session-1",
        "command": "bash -lc 'echo ok'",
        "cwd": "/actor",
    }
    assert adapter.kwargs["controller_actor_id"] == "control-controller"
    assert adapter.kwargs["thread_id"] == "thread-1"
    assert adapter.kwargs["aggregation_ms"] == 120
    assert status == {
        "actor_id": "terminal-worker",
        "driver": "terminal-runtime",
        "session_id": "session-1",
        "controller_actor_id": "control-controller",
        "thread_id": "thread-1",
        "command": "bash -lc 'echo ok'",
        "cwd": "/actor",
        "pid": 321,
        "running": True,
    }

    await driver.stop(actor_id="terminal-worker")

    assert adapter.cancelled is True
    assert session_manager.closed_sessions == ["session-1"]


async def test_v2_build_embedded_runtime_gateway_registers_builtin_runtime_kinds(
    tmp_path,
) -> None:
    FakeTerminalAdapter.instances.clear()
    session_manager = FakeTerminalSessionManager()
    stack = build_embedded_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'embedded-runtime-stack.db'}",
        terminal_session_manager=session_manager,
        terminal_adapter_factory=FakeTerminalAdapter,
    )
    gateway = stack.gateway

    assert stack.runtime_drivers.list_runtime_kinds() == [
        "EXTERNAL",
        "LOCAL",
        "REMOTE",
        "TERMINAL",
    ]
    assert stack.local_driver is not None
    assert stack.terminal_driver is not None
    assert stack.remote_driver is not None
    assert stack.external_driver is not None

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
            "id": "shell",
            "kind": "TERMINAL",
            "name": "Shell",
            "slug": "shell",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-shell",
            "actor_id": "shell",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "TERMINAL",
            "placement": {
                "terminal_runtime": {
                    "command": "bash",
                    "controller_actor_id": "tyrant",
                }
            },
        })

        spawned = await stack.supervisor.spawn_actor_runtime("shell")
        status = await gateway.get_runtime_status("shell")

        assert spawned["attachment"]["kind"] == "TERMINAL"
        assert status is not None
        assert status["driver"] == "terminal-runtime"
        assert status["command"] == "bash"

        await stack.supervisor.stop_actor_runtime("shell")
    finally:
        await gateway.stop()


async def test_v2_embedded_runtime_gateway_routes_remote_runtime(tmp_path) -> None:
    stack = build_embedded_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'embedded-runtime-remote.db'}",
        enable_local=False,
        enable_terminal=False,
    )
    gateway = stack.gateway

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
            "id": "remote-node",
            "kind": "AGENT",
            "name": "Remote Node",
            "slug": "remote_node",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-remote-node",
            "actor_id": "remote-node",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "REMOTE",
            "placement": {
                "remote_runtime": {
                    "endpoint": {
                        "url": "wss://remote.example/ws",
                        "transport": "websocket",
                    },
                    "capabilities": ["events"],
                }
            },
        })

        spawned = await stack.supervisor.spawn_actor_runtime("remote-node")
        status = await gateway.get_runtime_status("remote-node")

        assert spawned["attachment"]["kind"] == "CONNECTOR"
        assert spawned["attachment"]["endpoint"]["driver"] == "remote-runtime"
        assert status is not None
        assert status["driver"] == "remote-runtime"
        assert status["endpoint"]["url"] == "wss://remote.example/ws"

        await stack.supervisor.stop_actor_runtime("remote-node")
    finally:
        await gateway.stop()


async def test_v2_embedded_runtime_gateway_stop_reclaims_external_runtime(tmp_path) -> None:
    stack = build_embedded_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'embedded-runtime-external-stop.db'}",
        enable_local=False,
        enable_terminal=False,
        enable_remote=False,
    )
    gateway = stack.gateway

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
            "id": "external-hook",
            "kind": "EXTERNAL",
            "name": "External Hook",
            "slug": "external_hook",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-external-hook",
            "actor_id": "external-hook",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "EXTERNAL",
            "placement": {
                "external_runtime": {
                    "endpoint": {
                        "url": "https://hooks.example/inbound",
                    },
                    "capabilities": ["ingest"],
                }
            },
        })

        await stack.supervisor.spawn_actor_runtime("external-hook")
        assert await stack.external_driver.status(actor_id="external-hook") is not None
    finally:
        await gateway.stop()

    assert await stack.external_driver.status(actor_id="external-hook") is None


async def test_v2_embedded_runtime_gateway_stop_reclaims_terminal_runtime(tmp_path) -> None:
    FakeTerminalAdapter.instances.clear()
    session_manager = FakeTerminalSessionManager()
    stack = build_embedded_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'embedded-runtime-stop.db'}",
        enable_local=False,
        terminal_session_manager=session_manager,
        terminal_adapter_factory=FakeTerminalAdapter,
    )
    gateway = stack.gateway

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
            "id": "shell-stop",
            "kind": "TERMINAL",
            "name": "Shell Stop",
            "slug": "shell_stop",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-shell-stop",
            "actor_id": "shell-stop",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "TERMINAL",
            "placement": {
                "terminal_runtime": {
                    "command": "bash",
                }
            },
        })

        await stack.supervisor.spawn_actor_runtime("shell-stop")
        adapter = FakeTerminalAdapter.instances[0]
        await asyncio.wait_for(adapter.run_started.wait(), timeout=1.0)
    finally:
        await gateway.stop()

    assert adapter.cancelled is True
    assert session_manager.closed_sessions == ["session-1"]
    assert await stack.terminal_driver.status(actor_id="shell-stop") is None


async def test_v2_projections_apply_expected_store_filters() -> None:
    store = FakeStore()
    deliveries = DeliveryProjection(store)
    sessions = SessionProjection(store)

    pending = await deliveries.pending_for_actor("actor-1", limit=10)
    acked = await deliveries.acked_for_actor("actor-1", limit=5)
    active = await sessions.active(surface="cli", limit=3)

    assert pending == [
        {"id": "delivery-1", "state": "PENDING"},
        {"id": "delivery-2", "state": "PENDING"},
    ]
    assert acked == [
        {"id": "delivery-1", "state": "ACKED"},
        {"id": "delivery-2", "state": "ACKED"},
    ]
    assert active == [{"id": "session-1", "surface": "cli"}]
    assert store.delivery_queries == [
        {
            "recipient_actor_id": "actor-1",
            "state": "PENDING",
            "limit": 10,
        },
        {
            "recipient_actor_id": "actor-1",
            "state": "ACKED",
            "limit": 5,
        },
    ]
    assert store.session_queries == [{
        "surface": "cli",
        "state": "OPEN",
        "limit": 3,
    }]


async def test_v2_local_runtime_driver_status_reports_none_when_not_running() -> None:
    driver = LocalRuntimeDriver()

    status = await driver.status(actor_id="missing")

    assert status is None


async def test_v2_build_local_runtime_gateway_supports_background_local_consumer(tmp_path) -> None:
    stack = build_local_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'local-runtime-stack.db'}",
        poll_interval=0.01,
        handler_factory=lambda actor_id, control, profile: (
            (lambda envelope, delivery: {
                "recipient_actor_ids": ["tyrant"],
                "payload": {"text": envelope["payload"]["text"].upper()},
            })
            if actor_id == "worker"
            else None
        ),
    )
    gateway = stack.gateway
    cli = CliConnector(gateway)
    runtime = ActorRuntime(gateway)

    assert stack.runtime_drivers.list_runtime_kinds() == ["LOCAL"]

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
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-worker",
            "actor_id": "worker",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })

        await stack.supervisor.spawn_actor_runtime("worker")

        result = await cli.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker",
            text="hello local runtime",
            external_key="cli:test",
        )

        for _ in range(50):
            pending = await gateway.list_pending_deliveries("tyrant")
            if pending:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("local runtime driver did not emit reply in time")

        seen: list[str] = []

        async def tyrant_handler(envelope: dict, delivery: dict) -> None:
            seen.append(envelope["payload"]["text"])
            return None

        processed = await runtime.consume_once(
            actor_id="tyrant",
            consumer_id="tyrant-runtime",
            handler=tyrant_handler,
        )
        status = await gateway.get_runtime_status("worker")

        assert result["envelope"]["payload"]["text"] == "hello local runtime"
        assert processed == 1
        assert seen == ["HELLO LOCAL RUNTIME"]
        assert status is not None
        assert status["driver"] == "local-runtime"
        assert status["mode"] == "consumer"
    finally:
        await gateway.stop()


async def test_v2_local_runtime_driver_uses_profile_bootstrap_policy(tmp_path) -> None:
    stack = build_local_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'profile-runtime-policy.db'}",
        poll_interval=0.01,
    )
    gateway = stack.gateway
    cli = CliConnector(gateway)
    runtime = ActorRuntime(gateway)

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
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_profile({
            "id": "profile-echo",
            "name": "echo-profile",
            "bootstrap_policy": {
                "local_runtime": {
                    "behavior": "echo",
                    "transform": "upper",
                    "prefix": "BOT: ",
                }
            },
        })
        await gateway.create_actor_control({
            "id": "control-worker",
            "actor_id": "worker",
            "manager_actor_id": "tyrant",
            "profile_id": "profile-echo",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })

        await stack.supervisor.spawn_actor_runtime("worker")
        await cli.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker",
            text="hello profile policy",
            external_key="cli:test",
        )

        for _ in range(50):
            pending = await gateway.list_pending_deliveries("tyrant")
            if pending:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("profile bootstrap policy did not produce reply in time")

        seen: list[str] = []

        async def tyrant_handler(envelope: dict, delivery: dict) -> None:
            seen.append(envelope["payload"]["text"])
            return None

        processed = await runtime.consume_once(
            actor_id="tyrant",
            consumer_id="tyrant-runtime",
            handler=tyrant_handler,
        )

        assert processed == 1
        assert seen == ["BOT: HELLO PROFILE POLICY"]
    finally:
        await gateway.stop()


async def test_v2_local_runtime_driver_actor_metadata_overrides_profile_policy(tmp_path) -> None:
    stack = build_local_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'actor-metadata-runtime-policy.db'}",
        poll_interval=0.01,
    )
    gateway = stack.gateway
    cli = CliConnector(gateway)
    runtime = ActorRuntime(gateway)

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
            "id": "worker-meta",
            "kind": "AGENT",
            "name": "Worker Meta",
            "slug": "worker_meta",
            "metadata": {
                "local_runtime": {
                    "behavior": "template_reply",
                    "text": "metadata reply",
                }
            },
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_profile({
            "id": "profile-default",
            "name": "default-profile",
            "bootstrap_policy": {
                "local_runtime": {
                    "behavior": "echo",
                    "transform": "upper",
                }
            },
        })
        await gateway.create_actor_control({
            "id": "control-worker-meta",
            "actor_id": "worker-meta",
            "manager_actor_id": "tyrant",
            "profile_id": "profile-default",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })

        await stack.supervisor.spawn_actor_runtime("worker-meta")
        await cli.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker-meta",
            text="hello actor metadata",
            external_key="cli:test",
        )

        for _ in range(50):
            pending = await gateway.list_pending_deliveries("tyrant")
            if pending:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("actor metadata policy did not produce reply in time")

        seen: list[str] = []

        async def tyrant_handler(envelope: dict, delivery: dict) -> None:
            seen.append(envelope["payload"]["text"])
            return None

        processed = await runtime.consume_once(
            actor_id="tyrant",
            consumer_id="tyrant-runtime",
            handler=tyrant_handler,
        )

        assert processed == 1
        assert seen == ["metadata reply"]
    finally:
        await gateway.stop()


async def test_v2_local_runtime_driver_blueprint_bootstrap_spec_overrides_profile_policy(
    tmp_path,
) -> None:
    stack = build_local_runtime_gateway(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'blueprint-runtime-policy.db'}",
        poll_interval=0.01,
    )
    gateway = stack.gateway
    cli = CliConnector(gateway)
    runtime = ActorRuntime(gateway)

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
            "id": "manager",
            "kind": "AGENT",
            "name": "Manager",
            "slug": "manager",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_profile({
            "id": "profile-default",
            "name": "default-profile",
            "bootstrap_policy": {
                "local_runtime": {
                    "behavior": "echo",
                    "transform": "upper",
                }
            },
        })
        requested = await gateway.request_actor_provision({
            "requested_by_actor_id": "tyrant",
            "work_item_owner_actor_id": "tyrant",
            "caller_actor_id": "tyrant",
            "blueprint": {
                "name": "Worker Spec",
                "parent_actor_id": "manager",
                "profile_id": "profile-default",
                "manager_actor_id": "tyrant",
                "bootstrap_spec": {
                    "local_runtime": {
                        "behavior": "template_reply",
                        "text": "spawned reply",
                    }
                },
                "metadata": {
                    "actor_id": "worker-spec",
                    "slug": "worker_spec",
                },
            },
        })
        await gateway.process_provision_work_item(
            requested["work_item"]["id"],
            executor_actor_id="factory",
            auto_reconcile=False,
        )

        await stack.supervisor.spawn_actor_runtime("worker-spec")
        await cli.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker-spec",
            text="hello blueprint policy",
            external_key="cli:test",
        )

        for _ in range(50):
            pending = await gateway.list_pending_deliveries("tyrant")
            if pending:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("blueprint bootstrap spec did not produce reply in time")

        seen: list[str] = []

        async def tyrant_handler(envelope: dict, delivery: dict) -> None:
            seen.append(envelope["payload"]["text"])
            return None

        processed = await runtime.consume_once(
            actor_id="tyrant",
            consumer_id="tyrant-runtime",
            handler=tyrant_handler,
        )

        assert processed == 1
        assert seen == ["spawned reply"]
    finally:
        await gateway.stop()
