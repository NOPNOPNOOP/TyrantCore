"""SystemAPI 单元测试。"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.models import (
    Endpoint,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Permissions,
    PermissionDeniedError,
    Relation,
    RelationType,
    Task,
    TaskStatus,
)
from tyrant_core.store.sqlite_store import SqliteStore
from tyrant_core.bus.event_bus import EventBus
from tyrant_core.bus.message_bus import MessageBus
from tyrant_core.api.system_api import SystemAPI

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


@pytest.fixture
def message_bus(store: SqliteStore, event_bus: EventBus) -> MessageBus:
    return MessageBus(store, event_bus)


@pytest.fixture
def api(store: SqliteStore, message_bus: MessageBus, event_bus: EventBus) -> SystemAPI:
    return SystemAPI(store, message_bus, event_bus)


async def _create_tyrant(store: SqliteStore) -> Node:
    """创建 Tyrant（is_owner=True）节点。"""
    tyrant = Node(
        id="tyrant",
        type=NodeType.HUMAN,
        name="Tyrant",
        is_owner=True,
        endpoint=Endpoint(protocol="local", address="cli"),
        status=NodeStatus.ONLINE,
        permissions=Permissions(
            create_agent=True,
            destroy_agent=True,
            recruit=True,
            audit=True,
            grant_permission=True,
        ),
        created_at=NOW,
    )
    async with store.transaction():
        await store.save_node(tyrant)
    return tyrant


# ── Agent 生命周期 ─────────────────────────────────────────────────────────


class TestAgentLifecycle:
    async def test_create_agent(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)

        agent = await api.create_agent(
            caller_id="tyrant",
            name="Agent-1",
            parent_id="tyrant",
        )

        assert agent.name == "Agent-1"
        assert agent.type == NodeType.LOCAL_AGENT
        assert agent.status == NodeStatus.ONLINE

        # 验证 REPORTS_TO 关系
        async with store.transaction():
            rel = await store.get_reports_to(agent.id)
        assert rel is not None
        assert rel.node_a == "tyrant"

    async def test_create_agent_auto_connected(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)

        a1 = await api.create_agent(caller_id="tyrant", name="A1", parent_id="tyrant")
        a2 = await api.create_agent(caller_id="tyrant", name="A2", parent_id="tyrant")

        # A1 和 A2 应自动 CONNECTED
        async with store.transaction():
            rels = await store.get_relations_for_node(a2.id)
        connected = [r for r in rels if r.type == RelationType.CONNECTED]
        assert len(connected) == 1
        sibling_ids = {connected[0].node_a, connected[0].node_b}
        assert a1.id in sibling_ids
        assert a2.id in sibling_ids

    async def test_create_agent_permission_denied(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        # 创建无权限的 agent
        agent = await api.create_agent(
            caller_id="tyrant", name="NoPerm", parent_id="tyrant"
        )

        with pytest.raises(PermissionDeniedError):
            await api.create_agent(
                caller_id=agent.id, name="Child", parent_id=agent.id,
            )

    async def test_destroy_agent(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        await api.destroy_agent(caller_id="tyrant", node_id=agent.id)

        async with store.transaction():
            got = await store.get_node(agent.id)
        assert got is None


# ── 人才池 ─────────────────────────────────────────────────────────────────


class TestTalentPool:
    async def test_release_and_recruit(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        # Release → SUSPENDED
        await api.talent_pool_release(caller_id="tyrant", node_id=agent.id)
        async with store.transaction():
            node = await store.get_node(agent.id)
        assert node.status == NodeStatus.SUSPENDED

        # 查询人才池
        pool = await api.talent_pool_query(caller_id="tyrant")
        assert len(pool) == 1
        assert pool[0].id == agent.id

        # Recruit → ONLINE
        rel = await api.talent_pool_recruit(
            caller_id="tyrant", node_id=agent.id, parent_id="tyrant"
        )
        async with store.transaction():
            node = await store.get_node(agent.id)
        assert node.status == NodeStatus.ONLINE
        assert rel.type == RelationType.REPORTS_TO


# ── 权限 ───────────────────────────────────────────────────────────────────


class TestPermissions:
    async def test_grant_permission(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        await api.grant_permission(
            caller_id="tyrant", target_id=agent.id, permission="create_agent"
        )

        async with store.transaction():
            node = await store.get_node(agent.id)
        assert node.permissions.create_agent is True

    async def test_grant_permission_denied_no_perm(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        with pytest.raises(PermissionDeniedError):
            await api.grant_permission(
                caller_id=agent.id, target_id="tyrant", permission="audit"
            )


# ── 任务 ───────────────────────────────────────────────────────────────────


class TestTasks:
    async def test_create_and_update_task(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)

        task = Task(
            id="t1", creator="tyrant", assignee="tyrant",
            content={"title": "Test"}, created_at=NOW, updated_at=NOW,
        )
        created = await api.task_create(caller_id="tyrant", task=task)
        assert created.id == "t1"

        updated = await api.task_update(
            caller_id="tyrant", task_id="t1", status=TaskStatus.IN_PROGRESS
        )
        assert updated.status == TaskStatus.IN_PROGRESS

    async def test_query_tasks(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        await api.task_create(
            caller_id="tyrant",
            task=Task(
                id="t1", creator="tyrant", assignee="tyrant",
                content={}, created_at=NOW, updated_at=NOW,
            ),
        )
        tasks = await api.task_query(caller_id="tyrant", assignee="tyrant")
        assert len(tasks) == 1


# ── 消息 ───────────────────────────────────────────────────────────────────


class TestMessages:
    async def test_send_message(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        msg = Message(
            id="m1", timestamp=NOW, from_node="tyrant",
            to=agent.id, content={"text": "hello"},
        )
        ids = await api.send_message(caller_id="tyrant", message=msg)
        assert len(ids) == 1


# ── 审计 ───────────────────────────────────────────────────────────────────


class TestAudit:
    async def test_audit_inspect(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        result = await api.audit_inspect(caller_id="tyrant", target_id=agent.id)
        assert result["node"]["name"] == "A1"
        assert len(result["relations"]) > 0  # 至少有 REPORTS_TO
