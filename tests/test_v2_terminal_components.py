from __future__ import annotations

import asyncio

import pytest

from tyrant_core.adapters.terminal_node import TerminalNodeAdapter
from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors import (
    TerminalAttachmentLifecycle,
    TerminalStdinPump,
    TerminalStdoutPump,
)
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


class PumpGateway:
    def __init__(
        self,
        *,
        lease_batches: list[list[dict]] | None = None,
        envelopes: dict[str, dict] | None = None,
    ) -> None:
        self._lease_batches = list(lease_batches or [])
        self._envelopes = dict(envelopes or {})
        self.ingested: list[dict] = []
        self.lease_calls: list[dict] = []
        self.acked: list[str] = []
        self.failed: list[dict] = []

    async def ingest(self, payload: dict) -> dict:
        self.ingested.append(dict(payload))
        return {"thread_id": payload.get("thread_id") or f"thread-{len(self.ingested)}"}

    async def lease_deliveries(
        self,
        *,
        actor_id: str,
        consumer_id: str,
        limit: int,
    ) -> list[dict]:
        self.lease_calls.append({
            "actor_id": actor_id,
            "consumer_id": consumer_id,
            "limit": limit,
        })
        if self._lease_batches:
            return self._lease_batches.pop(0)
        return []

    async def get_envelope(self, envelope_id: str) -> dict:
        return dict(self._envelopes[envelope_id])

    async def ack_delivery(self, delivery_id: str) -> None:
        self.acked.append(delivery_id)

    async def fail_delivery(self, delivery_id: str, *, error: dict) -> None:
        self.failed.append({
            "delivery_id": delivery_id,
            "error": dict(error),
        })


class FakeLoopPump:
    def __init__(self, result=None) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


async def test_v2_terminal_bridge_subcomponents_are_independently_usable(tmp_path) -> None:
    gateway = GatewayServer(
        store=SqliteStore(database_url=_db_url(tmp_path, "terminal-components.db"))
    )
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

        assert isinstance(adapter.lifecycle, TerminalAttachmentLifecycle)
        assert isinstance(adapter.stdout_pump, TerminalStdoutPump)
        assert isinstance(adapter.stdin_pump, TerminalStdinPump)

        await adapter.lifecycle.attach()
        online_presence = await gateway.get_presence("terminal:session-1")
        assert online_presence is not None
        assert online_presence["status"] == "ONLINE"

        stdout_task = asyncio.create_task(adapter.stdout_pump.run())
        stdin_task = asyncio.create_task(adapter.stdin_pump.run())

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

        thread_id = await asyncio.wait_for(stdout_task, timeout=2.0)
        await asyncio.wait_for(stdin_task, timeout=2.0)
        await adapter.lifecycle.detach()

        offline_presence = await gateway.get_presence("terminal:session-1")
        attachments = await gateway.query_attachments(actor_id="terminal-actor")
        controller_messages = await gateway.query_envelopes(sender_actor_id="terminal-actor")

        assert fake_session.writes == ["pwd\n"]
        assert len(attachments) == 1
        assert offline_presence is not None
        assert offline_presence["status"] == "OFFLINE"
        assert {item["kind"] for item in controller_messages} >= {
            "TERMINAL_OUTPUT",
            "TERMINAL_EXIT",
        }
        assert thread_id in {item["thread_id"] for item in controller_messages}
    finally:
        await gateway.stop()


async def test_v2_terminal_stdout_pump_skips_duplicate_exit_event() -> None:
    gateway = PumpGateway()
    session = FakeTerminalSession()
    session._reads = [""]
    session._alive = False
    pump = TerminalStdoutPump(
        gateway=gateway,
        actor_id="terminal-actor",
        controller_actor_id="controller",
        session=session,
        aggregation_ms=10,
    )
    pump._exit_notified = True

    thread_id = await pump.run(thread_id="thread-existing")

    assert thread_id == "thread-existing"
    assert gateway.ingested == []


async def test_v2_terminal_stdin_pump_acks_even_when_payload_has_no_text() -> None:
    gateway = PumpGateway(
        lease_batches=[
            [{"delivery": {"id": "delivery-1", "envelope_id": "env-1"}}],
            [],
        ],
        envelopes={"env-1": {"payload": {}}},
    )
    session = FakeTerminalSession()
    session._alive = False
    pump = TerminalStdinPump(
        gateway=gateway,
        actor_id="terminal-actor",
        consumer_id="consumer-1",
        session=session,
    )

    await pump.run()

    assert session.writes == []
    assert gateway.acked == ["delivery-1"]
    assert gateway.failed == []


async def test_v2_terminal_stdin_pump_reports_write_failures() -> None:
    gateway = PumpGateway(
        lease_batches=[
            [{"delivery": {"id": "delivery-1", "envelope_id": "env-1"}}],
        ],
        envelopes={"env-1": {"payload": {"text": "pwd\n"}}},
    )
    session = FakeTerminalSession()

    async def fail_write(_data: str) -> None:
        raise RuntimeError("write boom")

    session.write = fail_write
    pump = TerminalStdinPump(
        gateway=gateway,
        actor_id="terminal-actor",
        consumer_id="consumer-1",
        session=session,
    )

    with pytest.raises(RuntimeError, match="write boom"):
        await pump.run()

    assert gateway.acked == []
    assert gateway.failed == [{
        "delivery_id": "delivery-1",
        "error": {
            "error": "write boom",
            "adapter": "terminal",
            "session_id": "session-1",
        },
    }]


async def test_v2_terminal_bridge_internal_loops_delegate_to_pumps() -> None:
    adapter = TerminalNodeAdapter(
        gateway=object(),
        actor_id="terminal-actor",
        controller_actor_id="controller",
        session=FakeTerminalSession(),
        thread_id="thread-0",
    )
    stdout_pump = FakeLoopPump(result="thread-1")
    stdin_pump = FakeLoopPump(result=None)
    adapter.stdout_pump = stdout_pump
    adapter.stdin_pump = stdin_pump

    await adapter._stdout_loop()
    await adapter._stdin_loop()

    assert adapter.thread_id == "thread-1"
    assert stdout_pump.calls == [{"thread_id": "thread-0"}]
    assert stdin_pump.calls == [{}]
