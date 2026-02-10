"""models.py 单元测试。"""

from datetime import datetime, timezone

import pytest

from tyrant_core.models import (
    Delegation,
    Endpoint,
    Event,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Permission,
    PermissionEffect,
    Priority,
    RefType,
    Reference,
    Relation,
    RelationType,
    Scope,
    ScopeType,
    Task,
    TaskStatus,
    TyrantError,
    NodeNotFoundError,
    RelationNotFoundError,
    MessageNotFoundError,
    TaskNotFoundError,
    PermissionDeniedError,
    RoutingError,
    ValidationError,
)


# ── Enums ──────────────────────────────────────────────────────────────────


class TestEnums:
    def test_node_type_values(self):
        assert NodeType.HUMAN == "HUMAN"
        assert NodeType.LOCAL_AGENT == "LOCAL_AGENT"
        assert NodeType.EXTERNAL_AGENT == "EXTERNAL_AGENT"

    def test_node_status_values(self):
        assert NodeStatus.ONLINE == "ONLINE"
        assert NodeStatus.OFFLINE == "OFFLINE"
        assert NodeStatus.BUSY == "BUSY"
        assert NodeStatus.SUSPENDED == "SUSPENDED"

    def test_relation_type_values(self):
        assert RelationType.REPORTS_TO == "REPORTS_TO"
        assert RelationType.CONNECTED == "CONNECTED"

    def test_task_status_values(self):
        assert TaskStatus.PENDING == "PENDING"
        assert TaskStatus.IN_PROGRESS == "IN_PROGRESS"
        assert TaskStatus.BLOCKED == "BLOCKED"
        assert TaskStatus.COMPLETED == "COMPLETED"
        assert TaskStatus.FAILED == "FAILED"

    def test_priority_values(self):
        assert Priority.SYSTEM == 0
        assert Priority.URGENT == 1
        assert Priority.NORMAL == 2
        assert Priority.LOW == 3
        # 数字小 = 优先级高
        assert Priority.SYSTEM < Priority.URGENT < Priority.NORMAL < Priority.LOW

    def test_ref_type_values(self):
        assert RefType.MESSAGE == "MESSAGE"
        assert RefType.TASK == "TASK"
        assert RefType.NODE == "NODE"


# ── 嵌套结构 ───────────────────────────────────────────────────────────────


class TestNestedModels:
    def test_endpoint(self):
        ep = Endpoint(protocol="local", address="agent-1")
        assert ep.protocol == "local"
        assert ep.config == {}

    def test_endpoint_with_config(self):
        ep = Endpoint(protocol="adapter", address="slack-bot", config={"token": "xxx"})
        assert ep.config["token"] == "xxx"

    def test_scope_defaults(self):
        scope = Scope()
        assert scope.type == ScopeType.SELF_SUBTREE
        assert scope.targets == []

    def test_permission_defaults(self):
        perm = Permission(action="create_agent")
        assert perm.effect == PermissionEffect.ALLOW
        assert perm.scope.type == ScopeType.SELF_SUBTREE

    def test_permission_deny(self):
        perm = Permission(
            action="audit",
            effect=PermissionEffect.DENY,
            scope=Scope(type=ScopeType.NODES, targets=["node-x"]),
        )
        assert perm.effect == PermissionEffect.DENY
        assert perm.scope.targets == ["node-x"]

    def test_reference(self):
        ref = Reference(ref_type=RefType.MESSAGE, ref_id="msg-1", relation="reply_to")
        assert ref.ref_type == RefType.MESSAGE
        assert ref.relation == "reply_to"


# ── 核心实体 ───────────────────────────────────────────────────────────────


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


class TestNode:
    def test_create_node(self):
        node = Node(
            id="n1",
            type=NodeType.HUMAN,
            name="Tyrant",
            is_owner=True,
            endpoint=Endpoint(protocol="local", address="cli"),
            created_at=NOW,
        )
        assert node.id == "n1"
        assert node.is_owner is True
        assert node.status == NodeStatus.OFFLINE  # default
        assert node.permissions == []  # v3: empty list
        assert node.metadata == {}

    def test_node_serialization(self):
        node = Node(
            id="n1",
            type=NodeType.LOCAL_AGENT,
            name="Agent-1",
            endpoint=Endpoint(protocol="local", address="a1"),
            created_at=NOW,
        )
        data = node.model_dump()
        assert data["type"] == "LOCAL_AGENT"
        assert data["endpoint"]["protocol"] == "local"

        # 反序列化
        restored = Node(**data)
        assert restored.id == node.id
        assert restored.type == NodeType.LOCAL_AGENT


class TestRelation:
    def test_create_relation(self):
        rel = Relation(
            id="r1",
            type=RelationType.REPORTS_TO,
            node_a="boss",
            node_b="subordinate",
            created_at=NOW,
        )
        assert rel.type == RelationType.REPORTS_TO
        assert rel.config == {}


class TestMessage:
    def test_create_message(self):
        msg = Message(
            id="m1",
            timestamp=NOW,
            from_node="sender",
            to="receiver",
        )
        assert msg.type == "CHAT"
        assert msg.priority == Priority.NORMAL
        assert msg.requires_ack is False

    def test_message_multicast(self):
        msg = Message(
            id="m2",
            timestamp=NOW,
            from_node="sender",
            to=["r1", "r2", "r3"],
        )
        assert isinstance(msg.to, list)
        assert len(msg.to) == 3

    def test_message_with_references(self):
        msg = Message(
            id="m3",
            timestamp=NOW,
            from_node="sender",
            to="receiver",
            references=[
                Reference(ref_type=RefType.MESSAGE, ref_id="m1", relation="reply_to"),
            ],
        )
        assert len(msg.references) == 1


class TestTask:
    def test_create_task(self):
        task = Task(
            id="t1",
            creator="boss",
            assignee="worker",
            content={"title": "Do something"},
            created_at=NOW,
            updated_at=NOW,
        )
        assert task.status == TaskStatus.PENDING
        assert task.participants == []
        assert task.parent_task is None


class TestEvent:
    def test_create_event(self):
        evt = Event(
            id="e1",
            type="node.status_changed",
            timestamp=NOW,
            source="n1",
            data={"old": "OFFLINE", "new": "ONLINE"},
        )
        assert evt.type == "node.status_changed"
        assert evt.data["new"] == "ONLINE"


# ── 异常 ───────────────────────────────────────────────────────────────────


class TestDelegation:
    def test_create_delegation(self):
        d = Delegation(
            id="d1",
            delegator="boss",
            delegate="worker",
            action="create_agent",
            scope=Scope(type=ScopeType.SELF_SUBTREE),
            created_at=NOW,
        )
        assert d.one_time is False
        assert d.expires_at is None
        assert d.scope.type == ScopeType.SELF_SUBTREE


class TestExceptions:
    def test_hierarchy(self):
        assert issubclass(NodeNotFoundError, TyrantError)
        assert issubclass(RelationNotFoundError, TyrantError)
        assert issubclass(MessageNotFoundError, TyrantError)
        assert issubclass(TaskNotFoundError, TyrantError)
        assert issubclass(PermissionDeniedError, TyrantError)
        assert issubclass(RoutingError, TyrantError)
        assert issubclass(ValidationError, TyrantError)

    def test_catch_base(self):
        with pytest.raises(TyrantError):
            raise NodeNotFoundError("node-1 not found")
