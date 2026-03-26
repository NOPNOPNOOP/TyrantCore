from __future__ import annotations

import asyncio

import tyrant_core.gateway.connectors.terminal as terminal_connector_module
from tyrant_core.gateway.connectors import TerminalConnector


class FakeGateway:
    def __init__(self, actors: list[dict] | None = None) -> None:
        self.actors = {actor["id"]: dict(actor) for actor in actors or []}
        self.created_actors: list[dict] = []

    async def list_actors(self, id: str | None = None) -> list[dict]:
        if id is None:
            return list(self.actors.values())
        actor = self.actors.get(id)
        return [dict(actor)] if actor else []

    async def create_actor(self, payload: dict) -> dict:
        self.actors[payload["id"]] = dict(payload)
        self.created_actors.append(dict(payload))
        return dict(payload)


class FakeSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class FakeSessionManager:
    def __init__(self, *, return_session: bool = True) -> None:
        self.return_session = return_session
        self.sessions: dict[str, FakeSession] = {}
        self.create_calls: list[dict] = []
        self.closed_sessions: list[str] = []
        self.close_all_called = False
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
        if self.return_session:
            self.sessions[session_id] = FakeSession(session_id)
        return session_id

    def get_session(self, session_id: str) -> FakeSession | None:
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        self.closed_sessions.append(session_id)
        self.sessions.pop(session_id, None)

    async def close_all(self) -> None:
        self.close_all_called = True
        self.sessions.clear()


class FakeTerminalNodeAdapter:
    instances: list["FakeTerminalNodeAdapter"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.attachment_id = f"attachment:{kwargs['session'].session_id}"
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


async def test_v2_terminal_connector_requires_explicit_start() -> None:
    connector = TerminalConnector(FakeGateway(), session_manager=FakeSessionManager())

    try:
        await connector.spawn(
            actor_id="terminal-1",
            controller_actor_id="controller",
            command="bash",
        )
    except RuntimeError as exc:
        assert str(exc) == "TerminalConnector 尚未启动"
    else:
        raise AssertionError("expected RuntimeError")


async def test_v2_terminal_connector_spawn_creates_actor_and_stop_session(
    monkeypatch,
) -> None:
    FakeTerminalNodeAdapter.instances.clear()
    gateway = FakeGateway()
    session_manager = FakeSessionManager()
    connector = TerminalConnector(gateway, session_manager=session_manager)

    monkeypatch.setattr(
        terminal_connector_module,
        "TerminalNodeAdapter",
        FakeTerminalNodeAdapter,
    )

    await connector.start()
    result = await connector.spawn(
        actor_id="terminal-1",
        controller_actor_id="controller",
        command="bash -lc 'echo ok'",
        cwd="/tmp/work",
        env={"A": "1"},
        thread_id="thread-1",
    )
    adapter = FakeTerminalNodeAdapter.instances[0]
    await asyncio.wait_for(adapter.run_started.wait(), timeout=1.0)

    assert gateway.created_actors == [{
        "id": "terminal-1",
        "kind": "TERMINAL",
        "name": "terminal-1",
        "slug": "terminal-1",
        "bypass_policy": True,
    }]
    assert session_manager.create_calls == [{
        "command": "bash -lc 'echo ok'",
        "cwd": "/tmp/work",
        "env": {"A": "1"},
        "owner_agent_id": "terminal-1",
    }]
    assert result == {
        "session_id": "session-1",
        "actor_id": "terminal-1",
        "attachment_id": "attachment:session-1",
        "thread_id": "thread-1",
    }

    await connector.stop_session("session-1")

    assert adapter.cancelled is True
    assert session_manager.closed_sessions == ["session-1"]


async def test_v2_terminal_connector_stop_cancels_all_tasks(monkeypatch) -> None:
    FakeTerminalNodeAdapter.instances.clear()
    gateway = FakeGateway([{
        "id": "terminal-1",
        "kind": "TERMINAL",
    }])
    session_manager = FakeSessionManager()
    connector = TerminalConnector(gateway, session_manager=session_manager)

    monkeypatch.setattr(
        terminal_connector_module,
        "TerminalNodeAdapter",
        FakeTerminalNodeAdapter,
    )

    await connector.start()
    await connector.spawn(
        actor_id="terminal-1",
        controller_actor_id="controller",
        command="bash",
    )
    await connector.spawn(
        actor_id="terminal-1",
        controller_actor_id="controller",
        command="python",
    )

    for adapter in FakeTerminalNodeAdapter.instances:
        await asyncio.wait_for(adapter.run_started.wait(), timeout=1.0)

    await connector.stop()

    assert gateway.created_actors == []
    assert all(adapter.cancelled for adapter in FakeTerminalNodeAdapter.instances)
    assert session_manager.close_all_called is True
    assert connector._tasks == {}
    assert connector._started is False


async def test_v2_terminal_connector_spawn_fails_when_session_missing(
    monkeypatch,
) -> None:
    gateway = FakeGateway()
    session_manager = FakeSessionManager(return_session=False)
    connector = TerminalConnector(gateway, session_manager=session_manager)

    monkeypatch.setattr(
        terminal_connector_module,
        "TerminalNodeAdapter",
        FakeTerminalNodeAdapter,
    )

    await connector.start()

    try:
        await connector.spawn(
            actor_id="terminal-1",
            controller_actor_id="controller",
            command="bash",
        )
    except RuntimeError as exc:
        assert str(exc) == "terminal session not found: session-1"
    else:
        raise AssertionError("expected RuntimeError")


async def test_v2_terminal_connector_stop_session_without_task_still_closes_session() -> None:
    session_manager = FakeSessionManager()
    connector = TerminalConnector(FakeGateway(), session_manager=session_manager)

    await connector.stop_session("session-missing")

    assert session_manager.closed_sessions == ["session-missing"]
