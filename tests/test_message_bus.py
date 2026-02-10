"""MessageBus 单元测试。"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.models import (
    Endpoint,
    Message,
    Node,
    NodeType,
    Relation,
    RelationType,
    RoutingError,
)
from tyrant_core.store.sqlite_store import SqliteStore
from tyrant_core.bus.event_bus import EventBus
from tyrant_core.bus.message_bus import MessageBus

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _make_node(id: str, **kwargs) -> Node:
    defaults = dict(
        type=NodeType.LOCAL_AGENT,
        name=f"Node-{id}",
        endpoint=Endpoint(protocol="local", address=id),
        created_at=NOW,
    )
    defaults.update(kwargs)
    return Node(id=id, **defaults)


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


@pytest.fixture
def message_bus(store: SqliteStore, event_bus: EventBus) -> MessageBus:
    return MessageBus(store, event_bus)


async def _setup_connected_nodes(store: SqliteStore, a: str, b: str):
    """创建两个节点并建立 CONNECTED 关系。"""
    await store.save_node(_make_node(a))
    await store.save_node(_make_node(b))
    await store.save_relation(Relation(
        id=f"r-{a}-{b}", type=RelationType.CONNECTED,
        node_a=a, node_b=b, created_at=NOW,
    ))


async def _setup_reports_to(store: SqliteStore, boss: str, sub: str):
    """创建两个节点并建立 REPORTS_TO 关系。"""
    # 节点可能已存在，忽略
    if not await store.get_node(boss):
        await store.save_node(_make_node(boss))
    if not await store.get_node(sub):
        await store.save_node(_make_node(sub))
    await store.save_relation(Relation(
        id=f"r-{boss}-{sub}", type=RelationType.REPORTS_TO,
        node_a=boss, node_b=sub, created_at=NOW,
    ))


# ── 路由检查 ───────────────────────────────────────────────────────────────


class TestRouting:
    async def test_system_always_allowed(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await store.save_node(_make_node("target"))
        async with store.transaction():
            assert await message_bus._check_route("system", "target") is True

    async def test_connected_nodes_allowed(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await _setup_connected_nodes(store, "a", "b")
        async with store.transaction():
            assert await message_bus._check_route("a", "b") is True
            assert await message_bus._check_route("b", "a") is True

    async def test_reports_to_allowed(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await _setup_reports_to(store, "boss", "sub")
        async with store.transaction():
            assert await message_bus._check_route("boss", "sub") is True
            assert await message_bus._check_route("sub", "boss") is True

    async def test_no_relation_denied(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await store.save_node(_make_node("a"))
            await store.save_node(_make_node("b"))
        async with store.transaction():
            assert await message_bus._check_route("a", "b") is False


# ── 发送 ───────────────────────────────────────────────────────────────────


class TestSend:
    async def test_send_single(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await _setup_connected_nodes(store, "sender", "receiver")

        msg = Message(id="m1", timestamp=NOW, from_node="ignored", to="receiver")
        async with store.transaction():
            ids = await message_bus.send(msg, sender_id="sender")

        assert len(ids) == 1
        # from_node 被自动填充
        async with store.transaction():
            saved = await store.get_message(ids[0])
        assert saved.from_node == "sender"

    async def test_send_multicast(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await store.save_node(_make_node("s"))
            await store.save_node(_make_node("r1"))
            await store.save_node(_make_node("r2"))
            await store.save_relation(Relation(
                id="rel1", type=RelationType.CONNECTED,
                node_a="s", node_b="r1", created_at=NOW,
            ))
            await store.save_relation(Relation(
                id="rel2", type=RelationType.CONNECTED,
                node_a="s", node_b="r2", created_at=NOW,
            ))

        msg = Message(id="m1", timestamp=NOW, from_node="s", to=["r1", "r2"])
        async with store.transaction():
            ids = await message_bus.send(msg, sender_id="s")

        assert len(ids) == 2

    async def test_send_no_route_raises(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await store.save_node(_make_node("a"))
            await store.save_node(_make_node("b"))

        msg = Message(id="m1", timestamp=NOW, from_node="a", to="b")
        with pytest.raises(RoutingError):
            async with store.transaction():
                await message_bus.send(msg, sender_id="a")

    async def test_multicast_skips_unreachable(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await store.save_node(_make_node("s"))
            await store.save_node(_make_node("r1"))
            await store.save_node(_make_node("r2"))
            # 只和 r1 有关系
            await store.save_relation(Relation(
                id="rel1", type=RelationType.CONNECTED,
                node_a="s", node_b="r1", created_at=NOW,
            ))

        msg = Message(id="m1", timestamp=NOW, from_node="s", to=["r1", "r2"])
        async with store.transaction():
            ids = await message_bus.send(msg, sender_id="s")

        # 只有 r1 能收到
        assert len(ids) == 1


# ── Inbox ──────────────────────────────────────────────────────────────────


class TestInbox:
    async def test_inbox_receives_message(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await _setup_connected_nodes(store, "s", "r")

        msg = Message(id="m1", timestamp=NOW, from_node="s", to="r")
        async with store.transaction():
            await message_bus.send(msg, sender_id="s")

        # 内存 queue 中应有消息
        assert "r" in message_bus._inboxes
        assert not message_bus._inboxes["r"].empty()

    async def test_get_inbox_from_store(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await _setup_connected_nodes(store, "s", "r")

        msg = Message(id="m1", timestamp=NOW, from_node="s", to="r")
        async with store.transaction():
            await message_bus.send(msg, sender_id="s")

        async with store.transaction():
            inbox = await message_bus.get_inbox("r")
        assert len(inbox) == 1


# ── ACK ────────────────────────────────────────────────────────────────────


class TestACK:
    async def test_ack_message(self, store: SqliteStore, message_bus: MessageBus):
        async with store.transaction():
            await _setup_connected_nodes(store, "s", "r")

        msg = Message(id="m1", timestamp=NOW, from_node="s", to="r", requires_ack=True)
        async with store.transaction():
            await message_bus.send(msg, sender_id="s")
        async with store.transaction():
            await message_bus.ack("m1", "r")
        # 无异常即通过
