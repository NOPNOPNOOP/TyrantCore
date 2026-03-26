from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import tyrant_core.gateway.session_registry as session_registry_module
from tyrant_core.gateway.session_registry import SessionRegistry
from tyrant_core.models import Session, SessionMode, SessionState


def _session(**overrides) -> Session:
    now = datetime.now(timezone.utc)
    payload = {
        "id": "session-1",
        "surface": "cli",
        "binding_id": None,
        "actor_id": "actor-1",
        "external_key": "ext-1",
        "thread_id": None,
        "mode": SessionMode.PERSISTENT,
        "state": SessionState.OPEN,
        "metadata": {},
        "created_at": now,
        "last_seen_at": now,
        "closed_at": None,
    }
    payload.update(overrides)
    return Session(**payload)


class FakeSessionStore:
    def __init__(self, sessions: list[Session] | None = None) -> None:
        self.sessions = {session.id: session for session in sessions or []}
        self.query_calls: list[dict] = []
        self.saved: list[Session] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Session]:
        self.query_calls.append({
            "actor_id": actor_id,
            "surface": surface,
            "external_key": external_key,
            "state": state,
            "limit": limit,
        })
        result = []
        for session in self.sessions.values():
            if actor_id is not None and session.actor_id != actor_id:
                continue
            if surface is not None and session.surface != surface:
                continue
            if external_key is not None and session.external_key != external_key:
                continue
            if state is not None and session.state != state:
                continue
            result.append(session)
        return result[:limit]

    async def save_session(self, session: Session) -> None:
        self.sessions[session.id] = session
        self.saved.append(session)

    async def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)


async def test_v2_session_registry_reuses_matching_persistent_session() -> None:
    store = FakeSessionStore([
        _session(
            id="session-existing",
            metadata={"origin": "old"},
        )
    ])
    registry = SessionRegistry(store)

    result = await registry.open_session(
        surface="cli",
        actor_id="actor-1",
        external_key="ext-1",
        thread_id="thread-1",
        mode="PERSISTENT",
        binding_id="binding-1",
        metadata={"fresh": True},
    )

    assert result.id == "session-existing"
    assert result.thread_id == "thread-1"
    assert result.binding_id == "binding-1"
    assert result.state == SessionState.OPEN
    assert result.metadata == {"origin": "old", "fresh": True}
    assert store.query_calls == [{
        "actor_id": None,
        "surface": "cli",
        "external_key": "ext-1",
        "state": None,
        "limit": 20,
    }]
    assert store.saved[-1].id == "session-existing"


async def test_v2_session_registry_creates_when_existing_session_not_reusable(
    monkeypatch,
) -> None:
    store = FakeSessionStore([
        _session(id="closed", state=SessionState.CLOSED),
        _session(id="other-actor", actor_id="actor-2"),
        _session(id="ephemeral", mode=SessionMode.EPHEMERAL),
        _session(id="bound", binding_id="binding-x"),
    ])
    registry = SessionRegistry(store)

    generated_ids = iter(["new-persistent", "new-ephemeral"])
    monkeypatch.setattr(session_registry_module, "uuid4", lambda: next(generated_ids))

    created = await registry.open_session(
        surface="cli",
        actor_id="actor-1",
        external_key="ext-1",
        thread_id="thread-2",
        binding_id="binding-y",
        metadata={"x": 1},
    )
    ephemeral = await registry.open_session(
        surface="cli",
        actor_id="actor-1",
        external_key="ext-1",
        mode="EPHEMERAL",
    )

    assert created.id == "new-persistent"
    assert created.mode == SessionMode.PERSISTENT
    assert created.thread_id == "thread-2"
    assert created.binding_id == "binding-y"
    assert created.metadata == {"x": 1}
    assert ephemeral.id == "new-ephemeral"
    assert ephemeral.mode == SessionMode.EPHEMERAL


async def test_v2_session_registry_touch_bind_close_and_query() -> None:
    existing = _session(id="session-1", thread_id="thread-old")
    store = FakeSessionStore([existing])
    registry = SessionRegistry(store)

    touched = await registry.touch("session-1")
    missing_touch = await registry.touch("missing")
    bound = await registry.bind_thread("session-1", "thread-new")
    missing_bind = await registry.bind_thread("missing", "thread-x")
    closed = await registry.close("session-1")
    missing_close = await registry.close("missing")
    queried = await registry.query(
        actor_id="actor-1",
        surface="cli",
        external_key="ext-1",
        state="CLOSED",
        limit=5,
    )
    fetched = await registry.get("session-1")
    missing = await registry.get("missing")

    assert touched is not None
    assert touched.state == SessionState.OPEN
    assert missing_touch is None
    assert bound is not None
    assert bound.thread_id == "thread-new"
    assert missing_bind is None
    assert closed is not None
    assert closed.state == SessionState.CLOSED
    assert closed.closed_at is not None
    assert missing_close is None
    assert queried == [closed]
    assert fetched == closed
    assert missing is None
    assert store.query_calls[-1] == {
        "actor_id": "actor-1",
        "surface": "cli",
        "external_key": "ext-1",
        "state": "CLOSED",
        "limit": 5,
    }
