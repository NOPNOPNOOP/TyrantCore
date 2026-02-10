"""SqliteStore 单元测试，使用内存 SQLite。"""

from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.models import (
    Endpoint,
    Event,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Permissions,
    Priority,
    Reference,
    RefType,
    Relation,
    RelationType,
    Task,
    TaskStatus,
)
from tyrant_core.store.sqlite_store import SqliteStore

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    s = SqliteStore(engine)
    await s.initialize()
    yield s
    await s.close()


def _make_node(id: str, **kwargs) -> Node:
    defaults = dict(
        type=NodeType.LOCAL_AGENT,
        name=f"Node-{id}",
        endpoint=Endpoint(protocol="local", address=id),
        created_at=NOW,
    )
    defaults.update(kwargs)
    return Node(id=id, **defaults)


def _make_relation(id: str, type: RelationType, node_a: str, node_b: str) -> Relation:
    return Relation(id=id, type=type, node_a=node_a, node_b=node_b, created_at=NOW)


# ── Node CRUD ──────────────────────────────────────────────────────────────


class TestNodeCRUD:
    async def test_save_and_get(self, store: SqliteStore):
        node = _make_node("n1", name="Agent-1", is_owner=True)
        async with store.transaction():
            await store.save_node(node)
        async with store.transaction():
            got = await store.get_node("n1")
        assert got is not None
        assert got.id == "n1"
        assert got.name == "Agent-1"
        assert got.is_owner is True
        assert got.status == NodeStatus.OFFLINE

    async def test_get_nonexistent(self, store: SqliteStore):
        async with store.transaction():
            got = await store.get_node("nope")
        assert got is None

    async def test_update_node(self, store: SqliteStore):
        node = _make_node("n1")
        async with store.transaction():
            await store.save_node(node)
        async with store.transaction():
            await store.update_node("n1", status=NodeStatus.ONLINE, name="Updated")
        async with store.transaction():
            got = await store.get_node("n1")
        assert got.status == NodeStatus.ONLINE
        assert got.name == "Updated"

    async def test_update_metadata(self, store: SqliteStore):
        node = _make_node("n1")
        async with store.transaction():
            await store.save_node(node)
        async with store.transaction():
            await store.update_node("n1", metadata={"key": "value"})
        async with store.transaction():
            got = await store.get_node("n1")
        assert got.metadata == {"key": "value"}

    async def test_delete_node(self, store: SqliteStore):
        node = _make_node("n1")
        async with store.transaction():
            await store.save_node(node)
            await store.delete_node("n1")
        async with store.transaction():
            got = await store.get_node("n1")
        assert got is None

    async def test_list_nodes(self, store: SqliteStore):
        async with store.transaction():
            await store.save_node(_make_node("n1", type=NodeType.HUMAN))
            await store.save_node(_make_node("n2", type=NodeType.LOCAL_AGENT))
            await store.save_node(_make_node("n3", type=NodeType.LOCAL_AGENT))

        async with store.transaction():
            all_nodes = await store.list_nodes()
            assert len(all_nodes) == 3

            agents = await store.list_nodes(type=NodeType.LOCAL_AGENT)
            assert len(agents) == 2


# ── Relation CRUD ──────────────────────────────────────────────────────────


class TestRelationCRUD:
    async def test_save_and_get(self, store: SqliteStore):
        async with store.transaction():
            await store.save_node(_make_node("boss"))
            await store.save_node(_make_node("sub"))
            rel = _make_relation("r1", RelationType.REPORTS_TO, "boss", "sub")
            await store.save_relation(rel)

        async with store.transaction():
            got = await store.get_relation("r1")
        assert got is not None
        assert got.type == RelationType.REPORTS_TO
        assert got.node_a == "boss"
        assert got.node_b == "sub"

    async def test_get_relations_for_node(self, store: SqliteStore):
        async with store.transaction():
            await store.save_node(_make_node("a"))
            await store.save_node(_make_node("b"))
            await store.save_node(_make_node("c"))
            await store.save_relation(_make_relation("r1", RelationType.REPORTS_TO, "a", "b"))
            await store.save_relation(_make_relation("r2", RelationType.CONNECTED, "b", "c"))

        async with store.transaction():
            rels = await store.get_relations_for_node("b")
        assert len(rels) == 2

    async def test_get_reports_to(self, store: SqliteStore):
        async with store.transaction():
            await store.save_node(_make_node("boss"))
            await store.save_node(_make_node("sub"))
            await store.save_relation(_make_relation("r1", RelationType.REPORTS_TO, "boss", "sub"))

        async with store.transaction():
            rel = await store.get_reports_to("sub")
        assert rel is not None
        assert rel.node_a == "boss"

    async def test_get_subordinates(self, store: SqliteStore):
        async with store.transaction():
            await store.save_node(_make_node("boss"))
            await store.save_node(_make_node("s1"))
            await store.save_node(_make_node("s2"))
            await store.save_relation(_make_relation("r1", RelationType.REPORTS_TO, "boss", "s1"))
            await store.save_relation(_make_relation("r2", RelationType.REPORTS_TO, "boss", "s2"))

        async with store.transaction():
            subs = await store.get_subordinates("boss")
        assert len(subs) == 2

    async def test_get_subtree(self, store: SqliteStore):
        # boss → mid → leaf
        async with store.transaction():
            await store.save_node(_make_node("boss"))
            await store.save_node(_make_node("mid"))
            await store.save_node(_make_node("leaf"))
            await store.save_relation(_make_relation("r1", RelationType.REPORTS_TO, "boss", "mid"))
            await store.save_relation(_make_relation("r2", RelationType.REPORTS_TO, "mid", "leaf"))

        async with store.transaction():
            tree = await store.get_subtree_node_ids("boss")
        assert tree == {"boss", "mid", "leaf"}

    async def test_delete_relation(self, store: SqliteStore):
        async with store.transaction():
            await store.save_node(_make_node("a"))
            await store.save_node(_make_node("b"))
            await store.save_relation(_make_relation("r1", RelationType.CONNECTED, "a", "b"))
            await store.delete_relation("r1")

        async with store.transaction():
            got = await store.get_relation("r1")
        assert got is None


# ── Message CRUD ───────────────────────────────────────────────────────────


class TestMessageCRUD:
    async def test_save_and_get(self, store: SqliteStore):
        msg = Message(
            id="m1", timestamp=NOW, from_node="sender", to="receiver",
            content={"text": "hello"},
        )
        async with store.transaction():
            await store.save_message(msg)
        async with store.transaction():
            got = await store.get_message("m1")
        assert got is not None
        assert got.from_node == "sender"
        assert got.content["text"] == "hello"

    async def test_get_inbox_priority_order(self, store: SqliteStore):
        async with store.transaction():
            await store.save_message(Message(
                id="m1", timestamp=NOW, from_node="s", to="r", priority=Priority.LOW,
            ))
            await store.save_message(Message(
                id="m2", timestamp=NOW, from_node="s", to="r", priority=Priority.URGENT,
            ))
            await store.save_message(Message(
                id="m3", timestamp=NOW, from_node="s", to="r", priority=Priority.SYSTEM,
            ))

        async with store.transaction():
            inbox = await store.get_inbox("r")
        # SYSTEM(0) < URGENT(1) < LOW(3)
        assert [m.id for m in inbox] == ["m3", "m2", "m1"]

    async def test_mark_delivered_and_acked(self, store: SqliteStore):
        async with store.transaction():
            await store.save_message(Message(
                id="m1", timestamp=NOW, from_node="s", to="r", requires_ack=True,
            ))
            await store.mark_delivered("m1", "r")
            await store.mark_acked("m1", "r")
        # 无异常即通过


# ── Task CRUD ──────────────────────────────────────────────────────────────


class TestTaskCRUD:
    async def test_save_and_get(self, store: SqliteStore):
        task = Task(
            id="t1", creator="boss", assignee="worker",
            content={"title": "Test"}, created_at=NOW, updated_at=NOW,
        )
        async with store.transaction():
            await store.save_task(task)
        async with store.transaction():
            got = await store.get_task("t1")
        assert got is not None
        assert got.assignee == "worker"
        assert got.status == TaskStatus.PENDING

    async def test_update_task(self, store: SqliteStore):
        task = Task(
            id="t1", creator="boss", assignee="worker",
            content={}, created_at=NOW, updated_at=NOW,
        )
        async with store.transaction():
            await store.save_task(task)
        async with store.transaction():
            await store.update_task("t1", status=TaskStatus.IN_PROGRESS)
        async with store.transaction():
            got = await store.get_task("t1")
        assert got.status == TaskStatus.IN_PROGRESS

    async def test_query_tasks(self, store: SqliteStore):
        async with store.transaction():
            await store.save_task(Task(
                id="t1", creator="boss", assignee="w1",
                content={}, created_at=NOW, updated_at=NOW,
            ))
            await store.save_task(Task(
                id="t2", creator="boss", assignee="w2",
                status=TaskStatus.COMPLETED,
                content={}, created_at=NOW, updated_at=NOW,
            ))

        async with store.transaction():
            pending = await store.query_tasks(status=TaskStatus.PENDING)
            assert len(pending) == 1
            assert pending[0].id == "t1"

            by_assignee = await store.query_tasks(assignee="w2")
            assert len(by_assignee) == 1


# ── Event CRUD ─────────────────────────────────────────────────────────────


class TestEventCRUD:
    async def test_append_and_get(self, store: SqliteStore):
        evt = Event(id="e1", type="node.created", timestamp=NOW, source="system")
        async with store.transaction():
            await store.append_event(evt)
        async with store.transaction():
            events = await store.get_events(since=NOW - timedelta(hours=1))
        assert len(events) == 1
        assert events[0].type == "node.created"

    async def test_get_events_with_type_filter(self, store: SqliteStore):
        async with store.transaction():
            await store.append_event(Event(
                id="e1", type="node.created", timestamp=NOW, source="system",
            ))
            await store.append_event(Event(
                id="e2", type="task.updated", timestamp=NOW, source="system",
            ))

        async with store.transaction():
            events = await store.get_events(
                since=NOW - timedelta(hours=1), types=["node.created"]
            )
        assert len(events) == 1
        assert events[0].id == "e1"


# ── Transaction ────────────────────────────────────────────────────────────


class TestTransaction:
    async def test_requires_transaction(self, store: SqliteStore):
        with pytest.raises(RuntimeError, match="transaction"):
            await store.save_node(_make_node("n1"))

    async def test_rollback_on_error(self, store: SqliteStore):
        with pytest.raises(ValueError):
            async with store.transaction():
                await store.save_node(_make_node("n1"))
                raise ValueError("boom")

        async with store.transaction():
            got = await store.get_node("n1")
        assert got is None
