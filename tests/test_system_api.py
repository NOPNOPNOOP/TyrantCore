"""SystemAPI 单元测试。"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.models import (
    ConversationTurn,
    Delegation,
    Endpoint,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Permission,
    PermissionDeniedError,
    PermissionEffect,
    Relation,
    RelationType,
    Scope,
    ScopeType,
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
        permissions=[],  # Tyrant: is_owner=True 隐含全局权限
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

        perm = Permission(
            action="create_agent",
            effect=PermissionEffect.ALLOW,
            scope=Scope(type=ScopeType.SELF_SUBTREE),
        )
        await api.grant_permission(
            caller_id="tyrant", target_id=agent.id, permission=perm,
        )

        async with store.transaction():
            node = await store.get_node(agent.id)
        assert len(node.permissions) == 1
        assert node.permissions[0].action == "create_agent"
        assert node.permissions[0].effect == PermissionEffect.ALLOW

    async def test_grant_permission_denied_no_perm(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        perm = Permission(action="audit")
        with pytest.raises(PermissionDeniedError):
            await api.grant_permission(
                caller_id=agent.id, target_id="tyrant", permission=perm,
            )

    async def test_scoped_permission_allows_subtree(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """agent 拥有 self_subtree 作用域的 create_agent，可以在自己子树下创建。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="Manager", parent_id="tyrant",
        )
        perm = Permission(
            action="create_agent",
            effect=PermissionEffect.ALLOW,
            scope=Scope(type=ScopeType.SELF_SUBTREE),
        )
        await api.grant_permission(
            caller_id="tyrant", target_id=agent.id, permission=perm,
        )

        # agent 在自己下方创建子节点 — 应成功
        child = await api.create_agent(
            caller_id=agent.id, name="Child", parent_id=agent.id,
        )
        assert child.name == "Child"

    async def test_deny_overrides_allow(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """deny 规则优先于 allow。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )

        # 先给 allow
        await api.grant_permission(
            caller_id="tyrant", target_id=agent.id,
            permission=Permission(
                action="audit",
                effect=PermissionEffect.ALLOW,
                scope=Scope(type=ScopeType.GLOBAL),
            ),
        )
        # 再给 deny
        await api.grant_permission(
            caller_id="tyrant", target_id=agent.id,
            permission=Permission(
                action="audit",
                effect=PermissionEffect.DENY,
                scope=Scope(type=ScopeType.GLOBAL),
            ),
        )

        # audit 应被拒绝
        with pytest.raises(PermissionDeniedError):
            await api.audit_inspect(caller_id=agent.id, target_id="tyrant")

    async def test_revoke_permission(
        self, store: SqliteStore, api: SystemAPI,
    ):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )

        perm = Permission(action="audit", scope=Scope(type=ScopeType.GLOBAL))
        await api.grant_permission(
            caller_id="tyrant", target_id=agent.id, permission=perm,
        )
        # revoke
        await api.revoke_permission(
            caller_id="tyrant", target_id=agent.id, action="audit",
        )
        async with store.transaction():
            node = await store.get_node(agent.id)
        assert len(node.permissions) == 0


# ── 委托 ─────────────────────────────────────────────────────────────────────


class TestDelegation:
    async def test_create_and_use_delegation(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """Tyrant 委托 create_agent 给 agent，agent 应能创建子节点。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="Delegatee", parent_id="tyrant",
        )

        # 无权限 — 应失败
        with pytest.raises(PermissionDeniedError):
            await api.create_agent(
                caller_id=agent.id, name="X", parent_id=agent.id,
            )

        # Tyrant 委托
        d = await api.create_delegation(
            caller_id="tyrant",
            delegate_id=agent.id,
            action="create_agent",
            scope=Scope(type=ScopeType.SELF_SUBTREE),
        )
        assert d.delegator == "tyrant"

        # 现在应成功
        child = await api.create_agent(
            caller_id=agent.id, name="DelegatedChild", parent_id=agent.id,
        )
        assert child.name == "DelegatedChild"

    async def test_revoke_delegation(
        self, store: SqliteStore, api: SystemAPI,
    ):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )
        d = await api.create_delegation(
            caller_id="tyrant",
            delegate_id=agent.id,
            action="create_agent",
            scope=Scope(type=ScopeType.SELF_SUBTREE),
        )

        await api.revoke_delegation(caller_id="tyrant", delegation_id=d.id)

        # 撤销后应失败
        with pytest.raises(PermissionDeniedError):
            await api.create_agent(
                caller_id=agent.id, name="X", parent_id=agent.id,
            )

    async def test_delegation_no_re_delegate(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """通过委托获得的权限不能再委托。"""
        await _create_tyrant(store)
        a1 = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )
        # Tyrant 委托给 A1
        await api.create_delegation(
            caller_id="tyrant",
            delegate_id=a1.id,
            action="create_agent",
            scope=Scope(type=ScopeType.SELF_SUBTREE),
        )

        # A1 试图再委托 — 应失败（A1 自身无 create_agent 权限，只有 delegation）
        a2 = await api.create_agent(
            caller_id=a1.id, name="A2", parent_id=a1.id,
        )
        with pytest.raises(PermissionDeniedError):
            await api.create_delegation(
                caller_id=a1.id,
                delegate_id=a2.id,
                action="create_agent",
                scope=Scope(type=ScopeType.SELF_SUBTREE),
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


# ── 任务完成通知 ────────────────────────────────────────────────────────────────


class TestTaskNotification:
    async def test_task_completed_notifies_creator(
        self, store: SqliteStore, api: SystemAPI, message_bus: MessageBus,
    ):
        """assignee 完成任务时，creator 应收到系统通知。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="Worker", parent_id="tyrant",
        )

        task = Task(
            id="t1", creator="tyrant", assignee=agent.id,
            content={"title": "Do something"}, created_at=NOW, updated_at=NOW,
        )
        await api.task_create(caller_id="tyrant", task=task)

        # assignee 完成任务（带 result）
        await api.task_update(
            caller_id=agent.id, task_id="t1",
            status=TaskStatus.COMPLETED, result={"output": "done"},
        )

        # creator (tyrant) 应收到通知消息
        async with store.transaction():
            inbox = await store.get_inbox("tyrant")
        notifications = [m for m in inbox if m.type == "NOTIFICATION"]
        assert len(notifications) == 1
        assert notifications[0].content["event"] == "task.completed"
        assert notifications[0].content["task_id"] == "t1"
        assert notifications[0].content["result"] == {"output": "done"}

    async def test_self_assigned_no_notification(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """自己完成自己创建的任务，不发通知。"""
        await _create_tyrant(store)

        task = Task(
            id="t1", creator="tyrant", assignee="tyrant",
            content={}, created_at=NOW, updated_at=NOW,
        )
        await api.task_create(caller_id="tyrant", task=task)
        await api.task_update(
            caller_id="tyrant", task_id="t1",
            status=TaskStatus.COMPLETED, result={"ok": True},
        )

        # tyrant 不应收到通知
        async with store.transaction():
            inbox = await store.get_inbox("tyrant")
        notifications = [m for m in inbox if m.type == "NOTIFICATION"]
        assert len(notifications) == 0

    async def test_task_result_persisted(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """更新 task 时 result 应被正确持久化。"""
        await _create_tyrant(store)

        task = Task(
            id="t1", creator="tyrant", assignee="tyrant",
            content={}, created_at=NOW, updated_at=NOW,
        )
        await api.task_create(caller_id="tyrant", task=task)
        await api.task_update(
            caller_id="tyrant", task_id="t1",
            status=TaskStatus.COMPLETED, result={"summary": "all good"},
        )

        async with store.transaction():
            got = await store.get_task("t1")
        assert got.result == {"summary": "all good"}
        assert got.status == TaskStatus.COMPLETED


# ── 消息 ─────────────────────────────────────────────────────────────────────


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


# ── 消息查询 ───────────────────────────────────────────────────────────────────


class TestQueryMessages:
    async def test_self_query(self, store: SqliteStore, api: SystemAPI):
        """Agent 可以查询自己的消息。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )
        # 发送消息
        msg = Message(
            id="m1", timestamp=NOW, from_node="tyrant",
            to=agent.id, content={"text": "hello"},
        )
        await api.send_message(caller_id="tyrant", message=msg)

        msgs = await api.query_messages(caller_id=agent.id, node_id=agent.id)
        assert len(msgs) == 1
        assert msgs[0].id == "m1"

    async def test_superior_can_query(self, store: SqliteStore, api: SystemAPI):
        """直接上级可以查询下属的消息。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )
        msg = Message(
            id="m1", timestamp=NOW, from_node="tyrant",
            to=agent.id, content={"text": "hello"},
        )
        await api.send_message(caller_id="tyrant", message=msg)

        msgs = await api.query_messages(caller_id="tyrant", node_id=agent.id)
        assert len(msgs) == 1

    async def test_no_permission_denied(self, store: SqliteStore, api: SystemAPI):
        """无权限的节点不能查询其他节点的消息。"""
        await _create_tyrant(store)
        a1 = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )
        a2 = await api.create_agent(
            caller_id="tyrant", name="A2", parent_id="tyrant"
        )

        with pytest.raises(PermissionDeniedError):
            await api.query_messages(caller_id=a1.id, node_id=a2.id)


# ── 对话历史查询 ─────────────────────────────────────────────────────────────


class TestQueryConversationHistory:
    async def test_self_query_history(self, store: SqliteStore, api: SystemAPI):
        """Agent 可以查询自己的对话历史。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )
        # 直接写入 turn
        async with store.transaction():
            await store.save_conversation_turn(ConversationTurn(
                id="t1", node_id=agent.id, role="user",
                content="hello", timestamp=NOW,
            ))

        turns = await api.query_conversation_history(
            caller_id=agent.id, target_id=agent.id,
        )
        assert len(turns) == 1
        assert turns[0].role == "user"

    async def test_superior_can_query_history(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """直接上级可以查询下属的对话历史。"""
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )
        async with store.transaction():
            await store.save_conversation_turn(ConversationTurn(
                id="t1", node_id=agent.id, role="model",
                content="response", timestamp=NOW,
            ))

        turns = await api.query_conversation_history(
            caller_id="tyrant", target_id=agent.id,
        )
        assert len(turns) == 1

    async def test_no_permission_denied_history(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """无权限的节点不能查询其他节点的对话历史。"""
        await _create_tyrant(store)
        a1 = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )
        a2 = await api.create_agent(
            caller_id="tyrant", name="A2", parent_id="tyrant"
        )

        with pytest.raises(PermissionDeniedError):
            await api.query_conversation_history(
                caller_id=a1.id, target_id=a2.id,
            )

    async def test_audit_delegation_allows_query(
        self, store: SqliteStore, api: SystemAPI,
    ):
        """通过委托获得 audit 权限后可以查询。"""
        await _create_tyrant(store)
        a1 = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )
        a2 = await api.create_agent(
            caller_id="tyrant", name="A2", parent_id="tyrant"
        )
        async with store.transaction():
            await store.save_conversation_turn(ConversationTurn(
                id="t1", node_id=a2.id, role="user",
                content="hello", timestamp=NOW,
            ))

        # 无权限
        with pytest.raises(PermissionDeniedError):
            await api.query_conversation_history(
                caller_id=a1.id, target_id=a2.id,
            )

        # Tyrant 委托 audit 给 A1
        await api.grant_permission(
            caller_id="tyrant", target_id=a1.id,
            permission=Permission(
                action="audit",
                scope=Scope(type=ScopeType.GLOBAL),
            ),
        )

        turns = await api.query_conversation_history(
            caller_id=a1.id, target_id=a2.id,
        )
        assert len(turns) == 1


# ── 审计 ─────────────────────────────────────────────────────────────────────


class TestAudit:
    async def test_audit_inspect(self, store: SqliteStore, api: SystemAPI):
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant"
        )

        result = await api.audit_inspect(caller_id="tyrant", target_id=agent.id)
        assert result["node"]["name"] == "A1"
        assert len(result["relations"]) > 0  # 至少有 REPORTS_TO
