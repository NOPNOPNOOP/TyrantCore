from __future__ import annotations

import asyncio

from tyrant_core.adapters.cli import CliAdapter
from tyrant_core.adapters.terminal_node import TerminalNodeAdapter
from tyrant_core.gateway import GatewayServer
from tyrant_core.runtime import ActorRuntime
from tyrant_core.store import SqliteStore


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


class FakeTerminalSession:
    def __init__(self) -> None:
        self.session_id = "session-1"
        self.command = "fake-shell"
        self.cwd = None
        self._alive = True
        self._reads = ["boot ok\n"]
        self.writes: list[str] = []

    async def read(self, timeout: float = 0.0) -> str:
        await asyncio.sleep(timeout)
        if self._reads:
            return self._reads.pop(0)
        return ""

    async def write(self, data: str) -> None:
        self.writes.append(data)

    def is_alive(self) -> bool:
        return self._alive


async def test_v2_cli_adapter_roundtrip(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "cli.db")))
    runtime = ActorRuntime(gateway)
    cli = CliAdapter(gateway, external_key="cli:test")
    await gateway.start()
    try:
        await cli.bootstrap()
        await cli.create_actor(
            actor_id="worker",
            kind="AGENT",
            name="Worker",
        )
        cli.target_actor_id = "worker"

        await cli.send_text("hello")

        async def worker_handler(envelope: dict, delivery: dict) -> dict:
            return {
                "recipient_actor_ids": ["tyrant"],
                "payload": {"text": envelope["payload"]["text"].upper()},
            }

        processed = await runtime.consume_once(
            actor_id="worker",
            consumer_id="worker-runtime",
            handler=worker_handler,
        )
        inbox = await cli.receive_once()
        sessions = await cli.list_sessions()
        bindings = await cli.list_bindings()

        assert processed == 1
        assert [item["envelope"]["payload"]["text"] for item in inbox] == ["HELLO"]
        assert len(sessions) == 1
        assert len(bindings) == 1
        assert bindings[0]["external_key"] == "cli:test"
        assert sessions[0]["thread_id"] is not None
    finally:
        await gateway.stop()


async def test_v2_terminal_node_adapter_bridges_gateway_io(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "terminal.db")))
    fake_session = FakeTerminalSession()
    adapter = TerminalNodeAdapter(
        gateway=gateway,
        actor_id="terminal-actor",
        controller_actor_id="controller",
        session=fake_session,
        aggregation_ms=10,
    )

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "controller",
            "kind": "TYRANT",
            "name": "Controller",
            "slug": "controller",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "terminal-actor",
            "kind": "TERMINAL",
            "name": "Terminal",
            "slug": "terminal-actor",
            "caller_actor_id": "controller",
        })

        task = asyncio.create_task(adapter.run())
        await asyncio.sleep(0.05)

        pending = await gateway.list_pending_deliveries("controller")
        assert len(pending) >= 1

        await gateway.ingest({
            "sender_actor_id": "controller",
            "recipient_actor_ids": ["terminal-actor"],
            "payload": {"text": "pwd\n"},
            "caller_actor_id": "controller",
        })

        await asyncio.sleep(0.1)
        fake_session._alive = False
        await asyncio.wait_for(task, timeout=2.0)

        presence = await gateway.get_presence("terminal:session-1")
        attachments = await gateway.query_attachments(actor_id="terminal-actor")
        controller_messages = await gateway.query_envelopes(sender_actor_id="terminal-actor")

        assert fake_session.writes == ["pwd\n"]
        assert len(attachments) == 1
        assert presence is not None
        assert presence["status"] == "OFFLINE"
        assert {item["kind"] for item in controller_messages} >= {
            "TERMINAL_OUTPUT",
            "TERMINAL_EXIT",
        }
    finally:
        await gateway.stop()
