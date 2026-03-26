from __future__ import annotations

import asyncio
import pytest

from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors.cli import CliConnector
from tyrant_core.runtime import ActorRuntime, build_embedded_runtime_gateway, build_local_runtime_gateway
from tyrant_core.store import SqliteStore
from tyrant_core.models import ValidationError


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def test_v2_gateway_recovers_session_and_delivery_state(tmp_path) -> None:
    database_url = _db_url(tmp_path, "v2-gateway-recovery.db")

    gateway1 = GatewayServer(store=SqliteStore(database_url=database_url))
    cli1 = CliConnector(gateway1)
    await gateway1.start()
    try:
        await gateway1.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway1.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })
        first = await cli1.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker",
            text="hello",
            external_key="cli:demo",
        )
        sessions = await gateway1.query_sessions(
            surface="cli",
            external_key="cli:demo",
        )
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] == first["thread_id"]
    finally:
        await gateway1.stop()

    gateway2 = GatewayServer(store=SqliteStore(database_url=database_url))
    cli2 = CliConnector(gateway2)
    runtime2 = ActorRuntime(gateway2)
    await gateway2.start()
    try:
        pending = await gateway2.list_pending_deliveries("worker")
        assert len(pending) == 1
        assert pending[0]["envelope_id"] == first["envelope"]["id"]

        sessions = await gateway2.query_sessions(
            surface="cli",
            external_key="cli:demo",
        )
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] == first["thread_id"]

        second = await cli2.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker",
            text="follow-up",
            external_key="cli:demo",
        )
        assert second["thread_id"] == first["thread_id"]

        seen_by_worker: list[str] = []

        async def worker_handler(envelope: dict, delivery: dict) -> None:
            seen_by_worker.append(envelope["payload"]["text"])
            return None

        processed = await runtime2.consume_once(
            actor_id="worker",
            consumer_id="worker-runtime",
            handler=worker_handler,
        )
        assert processed == 1
        assert seen_by_worker == ["hello"]
    finally:
        await gateway2.stop()

    gateway3 = GatewayServer(store=SqliteStore(database_url=database_url))
    await gateway3.start()
    try:
        acked = await gateway3.query_deliveries(actor_id="worker", state="ACKED")
        pending = await gateway3.list_pending_deliveries("worker")
        sessions = await gateway3.query_sessions(
            surface="cli",
            external_key="cli:demo",
        )

        assert len(acked) == 1
        assert acked[0]["envelope_id"] == first["envelope"]["id"]
        assert len(pending) == 1
        assert pending[0]["envelope_id"] == second["envelope"]["id"]
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] == first["thread_id"]
    finally:
        await gateway3.stop()


async def test_v2_gateway_ephemeral_session_is_not_reused(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "ephemeral.db")))
    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        first = await gateway.open_session(
            surface="cli",
            actor_id="tyrant",
            external_key="cli:ephemeral",
            mode="EPHEMERAL",
        )
        second = await gateway.open_session(
            surface="cli",
            actor_id="tyrant",
            external_key="cli:ephemeral",
            mode="EPHEMERAL",
        )

        assert first.id != second.id
    finally:
        await gateway.stop()


async def test_v2_local_runtime_gateway_recovers_active_runtime_on_restart(tmp_path) -> None:
    database_url = _db_url(tmp_path, "local-runtime-recovery.db")

    gateway1 = build_local_runtime_gateway(
        database_url=database_url,
        poll_interval=0.01,
    ).gateway
    await gateway1.start()
    try:
        await gateway1.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway1.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "metadata": {
                "local_runtime": {
                    "behavior": "template_reply",
                    "text": "recovered reply",
                }
            },
            "caller_actor_id": "tyrant",
        })
        await gateway1.create_actor_control({
            "id": "control-worker",
            "actor_id": "worker",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })
    finally:
        await gateway1.stop()

    stack2 = build_local_runtime_gateway(
        database_url=database_url,
        poll_interval=0.01,
    )
    gateway2 = stack2.gateway
    cli2 = CliConnector(gateway2)
    runtime2 = ActorRuntime(gateway2)
    await gateway2.start()
    try:
        for _ in range(50):
            status = await gateway2.get_runtime_status("worker")
            if status is not None:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("local runtime was not recovered on start")

        await cli2.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker",
            text="hello recovered local runtime",
            external_key="cli:recover-local",
        )

        for _ in range(50):
            pending = await gateway2.list_pending_deliveries("tyrant")
            if pending:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("recovered local runtime did not reply in time")

        seen: list[str] = []

        async def tyrant_handler(envelope: dict, delivery: dict) -> None:
            del delivery
            seen.append(envelope["payload"]["text"])
            return None

        processed = await runtime2.consume_once(
            actor_id="tyrant",
            consumer_id="tyrant-runtime",
            handler=tyrant_handler,
        )

        assert processed == 1
        assert seen == ["recovered reply"]
    finally:
        await gateway2.stop()


async def test_v2_embedded_runtime_gateway_recovers_terminal_runtime_on_restart(tmp_path) -> None:
    database_url = _db_url(tmp_path, "terminal-runtime-recovery.db")

    class FakeSession:
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

    class FakeSessionManager:
        def __init__(self) -> None:
            self.sessions: dict[str, FakeSession] = {}
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
            self.sessions[session_id] = FakeSession(session_id)
            return session_id

        def get_session(self, session_id: str):
            return self.sessions.get(session_id)

        async def close_session(self, session_id: str) -> None:
            self.closed_sessions.append(session_id)
            session = self.sessions.pop(session_id, None)
            if session is not None:
                session.alive = False

    class FakeAdapter:
        instances: list["FakeAdapter"] = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
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

    gateway1 = build_embedded_runtime_gateway(
        database_url=database_url,
        terminal_session_manager=FakeSessionManager(),
        terminal_adapter_factory=FakeAdapter,
    ).gateway
    await gateway1.start()
    try:
        await gateway1.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway1.create_actor({
            "id": "shell",
            "kind": "TERMINAL",
            "name": "Shell",
            "slug": "shell",
            "caller_actor_id": "tyrant",
        })
        await gateway1.create_actor_control({
            "id": "control-shell",
            "actor_id": "shell",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "TERMINAL",
            "placement": {
                "terminal_runtime": {
                    "command": "bash -lc 'echo recover'",
                    "controller_actor_id": "tyrant",
                }
            },
        })
    finally:
        await gateway1.stop()

    FakeAdapter.instances.clear()
    session_manager2 = FakeSessionManager()
    stack2 = build_embedded_runtime_gateway(
        database_url=database_url,
        terminal_session_manager=session_manager2,
        terminal_adapter_factory=FakeAdapter,
        enable_local=False,
    )
    await stack2.gateway.start()
    try:
        for _ in range(50):
            status = await stack2.gateway.get_runtime_status("shell")
            if status is not None:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("terminal runtime was not recovered on start")

        adapter = FakeAdapter.instances[0]
        await asyncio.wait_for(adapter.run_started.wait(), timeout=1.0)

        assert session_manager2.create_calls == [{
            "command": "bash -lc 'echo recover'",
            "cwd": None,
            "env": None,
            "owner_agent_id": "shell",
        }]
        assert status["driver"] == "terminal-runtime"
    finally:
        await stack2.gateway.stop()

    assert adapter.cancelled is True
    assert session_manager2.closed_sessions == ["session-1"]


async def test_v2_embedded_runtime_gateway_recovers_remote_runtime_on_restart(tmp_path) -> None:
    database_url = _db_url(tmp_path, "remote-runtime-recovery.db")

    gateway1 = build_embedded_runtime_gateway(
        database_url=database_url,
        enable_terminal=False,
    ).gateway
    await gateway1.start()
    try:
        await gateway1.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway1.create_actor({
            "id": "remote-recovered",
            "kind": "AGENT",
            "name": "Remote Recovered",
            "slug": "remote_recovered",
            "caller_actor_id": "tyrant",
        })
        await gateway1.create_actor_control({
            "id": "control-remote-recovered",
            "actor_id": "remote-recovered",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "REMOTE",
            "placement": {
                "remote_runtime": {
                    "endpoint": {
                        "url": "wss://recover.example/ws",
                        "transport": "websocket",
                    },
                    "capabilities": ["events", "deliver"],
                }
            },
        })
    finally:
        await gateway1.stop()

    stack2 = build_embedded_runtime_gateway(
        database_url=database_url,
        enable_local=False,
        enable_terminal=False,
        enable_external=False,
    )
    await stack2.gateway.start()
    try:
        for _ in range(50):
            status = await stack2.gateway.get_runtime_status("remote-recovered")
            if status is not None:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("remote runtime was not recovered on start")

        assert status["driver"] == "remote-runtime"
        assert status["endpoint"]["url"] == "wss://recover.example/ws"
        assert status["capabilities"] == ["events", "deliver"]
    finally:
        await stack2.gateway.stop()


async def test_v2_embedded_runtime_gateway_recovers_external_runtime_on_restart(tmp_path) -> None:
    database_url = _db_url(tmp_path, "external-runtime-recovery.db")

    gateway1 = build_embedded_runtime_gateway(
        database_url=database_url,
        enable_terminal=False,
        enable_remote=False,
    ).gateway
    await gateway1.start()
    try:
        await gateway1.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway1.create_actor({
            "id": "external-recovered",
            "kind": "EXTERNAL",
            "name": "External Recovered",
            "slug": "external_recovered",
            "caller_actor_id": "tyrant",
        })
        await gateway1.create_actor_control({
            "id": "control-external-recovered",
            "actor_id": "external-recovered",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "EXTERNAL",
            "placement": {
                "external_runtime": {
                    "endpoint": {
                        "url": "https://recover.example/hook",
                        "method": "POST",
                    },
                    "status": "DEGRADED",
                }
            },
        })
    finally:
        await gateway1.stop()

    stack2 = build_embedded_runtime_gateway(
        database_url=database_url,
        enable_local=False,
        enable_terminal=False,
        enable_remote=False,
    )
    await stack2.gateway.start()
    try:
        for _ in range(50):
            status = await stack2.gateway.get_runtime_status("external-recovered")
            if status is not None:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("external runtime was not recovered on start")

        assert status["driver"] == "external-runtime"
        assert status["endpoint"]["url"] == "https://recover.example/hook"
        assert status["presence_status"] == "DEGRADED"
    finally:
        await stack2.gateway.stop()


async def test_v2_local_runtime_gateway_recovery_records_unregistered_runtime_kind_without_failing_start(
    tmp_path,
) -> None:
    database_url = _db_url(tmp_path, "recovery-missing-driver.db")

    gateway1 = GatewayServer(store=SqliteStore(database_url=database_url))
    await gateway1.start()
    try:
        await gateway1.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway1.create_actor({
            "id": "shell-missing",
            "kind": "TERMINAL",
            "name": "Shell Missing",
            "slug": "shell_missing",
            "caller_actor_id": "tyrant",
        })
        await gateway1.create_actor_control({
            "id": "control-shell-missing",
            "actor_id": "shell-missing",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "TERMINAL",
        })
    finally:
        await gateway1.stop()

    stack2 = build_local_runtime_gateway(database_url=database_url)
    await stack2.gateway.start()
    try:
        with pytest.raises(ValidationError, match="runtime driver is not registered: TERMINAL"):
            await stack2.gateway.get_runtime_status("shell-missing")
        assert stack2.recovery_service.failures == [{
            "actor_id": "shell-missing",
            "error": "runtime driver is not registered: TERMINAL",
        }]
        assert stack2.recovery_service.last_error == "runtime driver is not registered: TERMINAL"
    finally:
        await stack2.gateway.stop()
