"""EventBus 单元测试。"""

from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.models import Endpoint, Event, Node, NodeType
from tyrant_core.store.sqlite_store import SqliteStore
from tyrant_core.bus.event_bus import EventBus

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    s = SqliteStore(engine)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def event_bus(store: SqliteStore) -> EventBus:
    return EventBus(store)


# ── 通配符匹配 ─────────────────────────────────────────────────────────────


class TestMatches:
    def test_exact_match(self):
        assert EventBus._matches("node.created", "node.created") is True
        assert EventBus._matches("node.created", "node.destroyed") is False

    def test_wildcard_all(self):
        assert EventBus._matches("*", "anything.here") is True
        assert EventBus._matches("*", "x") is True

    def test_wildcard_prefix(self):
        assert EventBus._matches("node.*", "node.created") is True
        assert EventBus._matches("node.*", "node.status_changed") is True
        assert EventBus._matches("node.*", "task.created") is False

    def test_wildcard_does_not_match_partial(self):
        # "node.*" 不应匹配 "node"（没有点后缀）
        assert EventBus._matches("node.*", "node") is False


# ── 发布与订阅 ─────────────────────────────────────────────────────────────


class TestPubSub:
    async def test_publish_triggers_callback(self, store: SqliteStore, event_bus: EventBus):
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        async with store.transaction():
            # 需要先创建节点才能持久化订阅
            await store.save_node(Node(
                id="n1", type=NodeType.LOCAL_AGENT, name="A1",
                endpoint=Endpoint(protocol="local", address="n1"),
                created_at=NOW,
            ))
            await event_bus.subscribe("n1", "node.created", handler)

            event = Event(id="e1", type="node.created", timestamp=NOW, source="system")
            await event_bus.publish(event)

        assert len(received) == 1
        assert received[0].id == "e1"

    async def test_wildcard_subscription(self, store: SqliteStore, event_bus: EventBus):
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        async with store.transaction():
            await store.save_node(Node(
                id="n1", type=NodeType.LOCAL_AGENT, name="A1",
                endpoint=Endpoint(protocol="local", address="n1"),
                created_at=NOW,
            ))
            await event_bus.subscribe("n1", "node.*", handler)

            await event_bus.publish(Event(
                id="e1", type="node.created", timestamp=NOW, source="system",
            ))
            await event_bus.publish(Event(
                id="e2", type="node.destroyed", timestamp=NOW, source="system",
            ))
            await event_bus.publish(Event(
                id="e3", type="task.created", timestamp=NOW, source="system",
            ))

        assert len(received) == 2  # node.created + node.destroyed

    async def test_unsubscribe(self, store: SqliteStore, event_bus: EventBus):
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        async with store.transaction():
            await store.save_node(Node(
                id="n1", type=NodeType.LOCAL_AGENT, name="A1",
                endpoint=Endpoint(protocol="local", address="n1"),
                created_at=NOW,
            ))
            await event_bus.subscribe("n1", "node.created", handler)
            await event_bus.unsubscribe("n1", "node.created")

            await event_bus.publish(Event(
                id="e1", type="node.created", timestamp=NOW, source="system",
            ))

        assert len(received) == 0

    async def test_no_duplicate_subscription(self, store: SqliteStore, event_bus: EventBus):
        call_count = 0

        async def handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1

        async with store.transaction():
            await store.save_node(Node(
                id="n1", type=NodeType.LOCAL_AGENT, name="A1",
                endpoint=Endpoint(protocol="local", address="n1"),
                created_at=NOW,
            ))
            await event_bus.subscribe("n1", "test.*", handler)
            await event_bus.subscribe("n1", "test.*", handler)  # 重复

            await event_bus.publish(Event(
                id="e1", type="test.foo", timestamp=NOW, source="system",
            ))

        assert call_count == 1

    async def test_callback_error_does_not_stop_others(self, store: SqliteStore, event_bus: EventBus):
        received: list[str] = []

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("boom")

        async def good_handler(event: Event) -> None:
            received.append(event.id)

        async with store.transaction():
            await store.save_node(Node(
                id="n1", type=NodeType.LOCAL_AGENT, name="A1",
                endpoint=Endpoint(protocol="local", address="n1"),
                created_at=NOW,
            ))
            await store.save_node(Node(
                id="n2", type=NodeType.LOCAL_AGENT, name="A2",
                endpoint=Endpoint(protocol="local", address="n2"),
                created_at=NOW,
            ))
            await event_bus.subscribe("n1", "test.event", bad_handler)
            await event_bus.subscribe("n2", "test.event", good_handler)

            await event_bus.publish(Event(
                id="e1", type="test.event", timestamp=NOW, source="system",
            ))

        assert "e1" in received


# ── 回放 ───────────────────────────────────────────────────────────────────


class TestReplay:
    async def test_replay_events(self, store: SqliteStore, event_bus: EventBus):
        async with store.transaction():
            await event_bus.publish(Event(
                id="e1", type="node.created", timestamp=NOW, source="system",
            ))
            await event_bus.publish(Event(
                id="e2", type="task.created",
                timestamp=NOW + timedelta(minutes=1), source="system",
            ))

        async with store.transaction():
            events = await event_bus.replay(since=NOW - timedelta(hours=1))
        assert len(events) == 2

    async def test_replay_with_type_filter(self, store: SqliteStore, event_bus: EventBus):
        async with store.transaction():
            await event_bus.publish(Event(
                id="e1", type="node.created", timestamp=NOW, source="system",
            ))
            await event_bus.publish(Event(
                id="e2", type="task.created", timestamp=NOW, source="system",
            ))

        async with store.transaction():
            events = await event_bus.replay(
                since=NOW - timedelta(hours=1), types=["node.created"]
            )
        assert len(events) == 1
        assert events[0].id == "e1"
