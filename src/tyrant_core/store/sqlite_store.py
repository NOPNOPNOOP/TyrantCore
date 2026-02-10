"""SQLAlchemy Core Table 定义 + SqliteStore 完整实现。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    select,
    insert,
    update,
    delete,
    or_,
    and_,
    JSON,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from tyrant_core.models import (
    ConversationTurn,
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
    Reference,
    RefType,
    Relation,
    RelationType,
    Scope,
    ScopeType,
    Task,
    TaskStatus,
)
from tyrant_core.store.base import Store

# ── Table 定义 ─────────────────────────────────────────────────────────────

metadata = MetaData()

nodes_table = Table(
    "nodes",
    metadata,
    Column("id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("name", String, nullable=False),
    Column("is_owner", Integer, nullable=False, default=0),
    Column("endpoint", JSON, nullable=False),
    Column("status", String, nullable=False, default="OFFLINE"),
    Column("permissions", JSON, nullable=False),
    Column("metadata_", JSON, nullable=False, default={}),
    Column("created_at", String, nullable=False),
)

relations_table = Table(
    "relations",
    metadata,
    Column("id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("node_a", String, ForeignKey("nodes.id"), nullable=False),
    Column("node_b", String, ForeignKey("nodes.id"), nullable=False),
    Column("config", JSON, nullable=False, default={}),
    Column("created_at", String, nullable=False),
    Index("idx_relations_node_a", "node_a"),
    Index("idx_relations_node_b", "node_b"),
    Index("idx_relations_type", "type"),
)

messages_table = Table(
    "messages",
    metadata,
    Column("id", String, primary_key=True),
    Column("timestamp", String, nullable=False),
    Column("expires_at", String, nullable=True),
    Column("from_node", String, nullable=False),
    Column("to_node", String, nullable=False),
    Column("type", String, nullable=False, default="CHAT"),
    Column("priority", Integer, nullable=False, default=2),
    Column("content", JSON, nullable=False, default={}),
    Column("references_json", JSON, nullable=False, default=[]),
    Column("thread_id", String, nullable=True),
    Column("requires_ack", Integer, nullable=False, default=0),
    Index("idx_messages_to", "to_node"),
    Index("idx_messages_from", "from_node"),
    Index("idx_messages_thread", "thread_id"),
)

deliveries_table = Table(
    "deliveries",
    metadata,
    Column("message_id", String, ForeignKey("messages.id"), primary_key=True),
    Column("node_id", String, ForeignKey("nodes.id"), primary_key=True),
    Column("delivered_at", String, nullable=True),
    Column("acked_at", String, nullable=True),
)

tasks_table = Table(
    "tasks",
    metadata,
    Column("id", String, primary_key=True),
    Column("creator", String, nullable=False),
    Column("assignee", String, nullable=False),
    Column("participants", JSON, nullable=False, default=[]),
    Column("status", String, nullable=False, default="PENDING"),
    Column("content", JSON, nullable=False, default={}),
    Column("result", JSON, nullable=False, default={}),
    Column("parent_task", String, nullable=True),
    Column("references_json", JSON, nullable=False, default=[]),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("idx_tasks_assignee", "assignee"),
    Index("idx_tasks_creator", "creator"),
    Index("idx_tasks_status", "status"),
)

events_table = Table(
    "events",
    metadata,
    Column("id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("source", String, nullable=False),
    Column("data", JSON, nullable=False, default={}),
    Index("idx_events_timestamp", "timestamp"),
    Index("idx_events_type", "type"),
)

delegations_table = Table(
    "delegations",
    metadata,
    Column("id", String, primary_key=True),
    Column("delegator", String, nullable=False),
    Column("delegate", String, nullable=False),
    Column("action", String, nullable=False),
    Column("scope", JSON, nullable=False),
    Column("one_time", Integer, nullable=False, default=0),
    Column("expires_at", String, nullable=True),
    Column("created_at", String, nullable=False),
    Index("idx_delegations_delegate", "delegate"),
    Index("idx_delegations_delegator", "delegator"),
)

conversation_turns_table = Table(
    "conversation_turns",
    metadata,
    Column("id", String, primary_key=True),
    Column("node_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("content", Text, nullable=False, default=""),
    Column("tool_calls", JSON, nullable=False, default=[]),
    Column("tool_result", JSON, nullable=False, default={}),
    Column("trigger_message_id", String, nullable=True),
    Column("timestamp", String, nullable=False),
    Index("idx_turns_node_id", "node_id"),
    Index("idx_turns_trigger", "trigger_message_id"),
)

subscriptions_table = Table(
    "subscriptions",
    metadata,
    Column("node_id", String, ForeignKey("nodes.id"), primary_key=True),
    Column("event_type", String, primary_key=True),
    Column("created_at", String, nullable=False),
)


# ── 转换辅助 ───────────────────────────────────────────────────────────────


def _dt_to_str(dt: datetime | None) -> str | None:
    """datetime → ISO 8601 字符串。"""
    return dt.isoformat() if dt else None


def _str_to_dt(s: str | None) -> datetime | None:
    """ISO 8601 字符串 → datetime。"""
    return datetime.fromisoformat(s) if s else None


def _node_to_row(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type.value,
        "name": node.name,
        "is_owner": int(node.is_owner),
        "endpoint": node.endpoint.model_dump(),
        "status": node.status.value,
        "permissions": [p.model_dump() for p in node.permissions],
        "metadata_": node.metadata,
        "created_at": _dt_to_str(node.created_at),
    }


def _row_to_node(row: Any) -> Node:
    perms_raw = row.permissions or []
    # v3: permissions 是 JSON 数组
    if isinstance(perms_raw, dict):
        perms = []  # v1 旧格式兼容：忽略旧 bool 权限
    else:
        perms = [Permission(**p) for p in perms_raw]
    return Node(
        id=row.id,
        type=NodeType(row.type),
        name=row.name,
        is_owner=bool(row.is_owner),
        endpoint=Endpoint(**row.endpoint),
        status=NodeStatus(row.status),
        permissions=perms,
        metadata=row.metadata_,
        created_at=_str_to_dt(row.created_at),  # type: ignore[arg-type]
    )


def _relation_to_row(rel: Relation) -> dict[str, Any]:
    return {
        "id": rel.id,
        "type": rel.type.value,
        "node_a": rel.node_a,
        "node_b": rel.node_b,
        "config": rel.config,
        "created_at": _dt_to_str(rel.created_at),
    }


def _row_to_relation(row: Any) -> Relation:
    return Relation(
        id=row.id,
        type=RelationType(row.type),
        node_a=row.node_a,
        node_b=row.node_b,
        config=row.config,
        created_at=_str_to_dt(row.created_at),  # type: ignore[arg-type]
    )


def _message_to_row(msg: Message, to_node: str) -> dict[str, Any]:
    """将 Message 转为单条数据库行（展开后的单个接收者）。"""
    return {
        "id": msg.id,
        "timestamp": _dt_to_str(msg.timestamp),
        "expires_at": _dt_to_str(msg.expires_at),
        "from_node": msg.from_node,
        "to_node": to_node,
        "type": msg.type,
        "priority": msg.priority.value,
        "content": msg.content,
        "references_json": [r.model_dump() for r in msg.references],
        "thread_id": msg.thread_id,
        "requires_ack": int(msg.requires_ack),
    }


def _row_to_message(row: Any) -> Message:
    refs = [
        Reference(
            ref_type=RefType(r["ref_type"]),
            ref_id=r["ref_id"],
            relation=r["relation"],
        )
        for r in (row.references_json or [])
    ]
    return Message(
        id=row.id,
        timestamp=_str_to_dt(row.timestamp),  # type: ignore[arg-type]
        expires_at=_str_to_dt(row.expires_at),
        from_node=row.from_node,
        to=row.to_node,
        type=row.type,
        priority=Priority(row.priority),
        content=row.content,
        references=refs,
        thread_id=row.thread_id,
        requires_ack=bool(row.requires_ack),
    )


def _task_to_row(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "creator": task.creator,
        "assignee": task.assignee,
        "participants": task.participants,
        "status": task.status.value,
        "content": task.content,
        "result": task.result,
        "parent_task": task.parent_task,
        "references_json": [r.model_dump() for r in task.references],
        "created_at": _dt_to_str(task.created_at),
        "updated_at": _dt_to_str(task.updated_at),
    }


def _row_to_task(row: Any) -> Task:
    refs = [
        Reference(
            ref_type=RefType(r["ref_type"]),
            ref_id=r["ref_id"],
            relation=r["relation"],
        )
        for r in (row.references_json or [])
    ]
    return Task(
        id=row.id,
        creator=row.creator,
        assignee=row.assignee,
        participants=row.participants or [],
        status=TaskStatus(row.status),
        content=row.content,
        result=row.result or {},
        parent_task=row.parent_task,
        references=refs,
        created_at=_str_to_dt(row.created_at),  # type: ignore[arg-type]
        updated_at=_str_to_dt(row.updated_at),  # type: ignore[arg-type]
    )


def _delegation_to_row(d: Delegation) -> dict[str, Any]:
    return {
        "id": d.id,
        "delegator": d.delegator,
        "delegate": d.delegate,
        "action": d.action,
        "scope": d.scope.model_dump(),
        "one_time": int(d.one_time),
        "expires_at": _dt_to_str(d.expires_at),
        "created_at": _dt_to_str(d.created_at),
    }


def _row_to_delegation(row: Any) -> Delegation:
    return Delegation(
        id=row.id,
        delegator=row.delegator,
        delegate=row.delegate,
        action=row.action,
        scope=Scope(**row.scope),
        one_time=bool(row.one_time),
        expires_at=_str_to_dt(row.expires_at),
        created_at=_str_to_dt(row.created_at),  # type: ignore[arg-type]
    )


def _turn_to_row(turn: ConversationTurn) -> dict[str, Any]:
    return {
        "id": turn.id,
        "node_id": turn.node_id,
        "role": turn.role,
        "content": turn.content,
        "tool_calls": turn.tool_calls,
        "tool_result": turn.tool_result,
        "trigger_message_id": turn.trigger_message_id,
        "timestamp": _dt_to_str(turn.timestamp),
    }


def _row_to_turn(row: Any) -> ConversationTurn:
    return ConversationTurn(
        id=row.id,
        node_id=row.node_id,
        role=row.role,
        content=row.content or "",
        tool_calls=row.tool_calls or [],
        tool_result=row.tool_result or {},
        trigger_message_id=row.trigger_message_id,
        timestamp=_str_to_dt(row.timestamp),  # type: ignore[arg-type]
    )


def _event_to_row(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "timestamp": _dt_to_str(event.timestamp),
        "source": event.source,
        "data": event.data,
    }


def _row_to_event(row: Any) -> Event:
    return Event(
        id=row.id,
        type=row.type,
        timestamp=_str_to_dt(row.timestamp),  # type: ignore[arg-type]
        source=row.source,
        data=row.data,
    )


# ── SqliteStore ────────────────────────────────────────────────────────────


class SqliteStore(Store):
    """基于 SQLAlchemy async + aiosqlite 的 Store 实现。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, class_=AsyncSession)
        self._current_session: AsyncSession | None = None

    def _session(self) -> AsyncSession:
        """获取当前事务中的 session，若不在事务中则抛出异常。"""
        if self._current_session is None:
            raise RuntimeError("必须在 store.transaction() 上下文中执行操作")
        return self._current_session

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()

    # ── Transaction ────────────────────────────────────────────────────────

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._session_factory() as session:
            async with session.begin():
                self._current_session = session
                try:
                    yield
                finally:
                    self._current_session = None

    # ── Node ───────────────────────────────────────────────────────────────

    async def save_node(self, node: Node) -> None:
        session = self._session()
        await session.execute(insert(nodes_table).values(**_node_to_row(node)))

    async def get_node(self, node_id: str) -> Node | None:
        session = self._session()
        result = await session.execute(
            select(nodes_table).where(nodes_table.c.id == node_id)
        )
        row = result.first()
        return _row_to_node(row) if row else None

    async def update_node(self, node_id: str, **fields: Any) -> None:
        session = self._session()
        values: dict[str, Any] = {}
        for key, val in fields.items():
            if key == "is_owner":
                values["is_owner"] = int(val)
            elif key == "endpoint":
                values["endpoint"] = val.model_dump() if hasattr(val, "model_dump") else val
            elif key == "permissions":
                # v3: permissions 是 list[Permission]
                if isinstance(val, list):
                    values["permissions"] = [
                        p.model_dump() if hasattr(p, "model_dump") else p
                        for p in val
                    ]
                else:
                    values["permissions"] = val
            elif key == "status":
                values["status"] = val.value if hasattr(val, "value") else val
            elif key == "type":
                values["type"] = val.value if hasattr(val, "value") else val
            elif key == "metadata":
                values["metadata_"] = val
            elif key == "created_at" and isinstance(val, datetime):
                values["created_at"] = _dt_to_str(val)
            else:
                values[key] = val
        if values:
            await session.execute(
                update(nodes_table).where(nodes_table.c.id == node_id).values(**values)
            )

    async def delete_node(self, node_id: str) -> None:
        session = self._session()
        await session.execute(
            delete(nodes_table).where(nodes_table.c.id == node_id)
        )

    async def list_nodes(self, **filters: Any) -> list[Node]:
        session = self._session()
        stmt = select(nodes_table)
        for key, val in filters.items():
            if key == "type":
                stmt = stmt.where(nodes_table.c.type == (val.value if hasattr(val, "value") else val))
            elif key == "status":
                stmt = stmt.where(nodes_table.c.status == (val.value if hasattr(val, "value") else val))
            elif key == "is_owner":
                stmt = stmt.where(nodes_table.c.is_owner == int(val))
        result = await session.execute(stmt)
        return [_row_to_node(row) for row in result.fetchall()]

    # ── Relation ───────────────────────────────────────────────────────────

    async def save_relation(self, relation: Relation) -> None:
        session = self._session()
        await session.execute(insert(relations_table).values(**_relation_to_row(relation)))

    async def get_relation(self, relation_id: str) -> Relation | None:
        session = self._session()
        result = await session.execute(
            select(relations_table).where(relations_table.c.id == relation_id)
        )
        row = result.first()
        return _row_to_relation(row) if row else None

    async def delete_relation(self, relation_id: str) -> None:
        session = self._session()
        await session.execute(
            delete(relations_table).where(relations_table.c.id == relation_id)
        )

    async def get_relations_for_node(self, node_id: str) -> list[Relation]:
        session = self._session()
        result = await session.execute(
            select(relations_table).where(
                or_(
                    relations_table.c.node_a == node_id,
                    relations_table.c.node_b == node_id,
                )
            )
        )
        return [_row_to_relation(row) for row in result.fetchall()]

    async def get_reports_to(self, node_id: str) -> Relation | None:
        """获取该节点的上级关系（node_b 为该节点的 REPORTS_TO）。"""
        session = self._session()
        result = await session.execute(
            select(relations_table).where(
                and_(
                    relations_table.c.type == RelationType.REPORTS_TO.value,
                    relations_table.c.node_b == node_id,
                )
            )
        )
        row = result.first()
        return _row_to_relation(row) if row else None

    async def get_subordinates(self, node_id: str) -> list[Relation]:
        """获取直接下属（node_a 为该节点的 REPORTS_TO）。"""
        session = self._session()
        result = await session.execute(
            select(relations_table).where(
                and_(
                    relations_table.c.type == RelationType.REPORTS_TO.value,
                    relations_table.c.node_a == node_id,
                )
            )
        )
        return [_row_to_relation(row) for row in result.fetchall()]

    async def get_subtree_node_ids(self, node_id: str) -> set[str]:
        """BFS 获取整棵子树的节点 ID。"""
        result: set[str] = set()
        queue = [node_id]
        while queue:
            current = queue.pop(0)
            result.add(current)
            subs = await self.get_subordinates(current)
            for rel in subs:
                if rel.node_b not in result:
                    queue.append(rel.node_b)
        return result

    # ── Message ────────────────────────────────────────────────────────────

    async def save_message(self, message: Message) -> None:
        session = self._session()
        # Message.to 已由上层展开为单个接收者字符串
        to_node = message.to if isinstance(message.to, str) else message.to[0]
        await session.execute(
            insert(messages_table).values(**_message_to_row(message, to_node))
        )

    async def get_message(self, message_id: str) -> Message | None:
        session = self._session()
        result = await session.execute(
            select(messages_table).where(messages_table.c.id == message_id)
        )
        row = result.first()
        return _row_to_message(row) if row else None

    async def get_inbox(self, node_id: str, limit: int = 50) -> list[Message]:
        """按 priority（升序，数字小优先级高）排序获取收件箱消息。"""
        session = self._session()
        result = await session.execute(
            select(messages_table)
            .where(messages_table.c.to_node == node_id)
            .order_by(messages_table.c.priority.asc(), messages_table.c.timestamp.desc())
            .limit(limit)
        )
        return [_row_to_message(row) for row in result.fetchall()]

    async def query_messages(
        self,
        node_id: str | None = None,
        from_node: str | None = None,
        to_node: str | None = None,
        type: str | None = None,
        limit: int = 50,
    ) -> list[Message]:
        session = self._session()
        stmt = select(messages_table)
        if node_id:
            stmt = stmt.where(
                or_(
                    messages_table.c.from_node == node_id,
                    messages_table.c.to_node == node_id,
                )
            )
        if from_node:
            stmt = stmt.where(messages_table.c.from_node == from_node)
        if to_node:
            stmt = stmt.where(messages_table.c.to_node == to_node)
        if type:
            stmt = stmt.where(messages_table.c.type == type)
        stmt = stmt.order_by(messages_table.c.timestamp.desc()).limit(limit)
        result = await session.execute(stmt)
        return [_row_to_message(row) for row in result.fetchall()]

    async def mark_delivered(self, message_id: str, node_id: str) -> None:
        session = self._session()
        now = _dt_to_str(datetime.now(timezone.utc))
        # upsert: 先尝试更新，不存在则插入
        result = await session.execute(
            select(deliveries_table).where(
                and_(
                    deliveries_table.c.message_id == message_id,
                    deliveries_table.c.node_id == node_id,
                )
            )
        )
        if result.first():
            await session.execute(
                update(deliveries_table)
                .where(
                    and_(
                        deliveries_table.c.message_id == message_id,
                        deliveries_table.c.node_id == node_id,
                    )
                )
                .values(delivered_at=now)
            )
        else:
            await session.execute(
                insert(deliveries_table).values(
                    message_id=message_id,
                    node_id=node_id,
                    delivered_at=now,
                    acked_at=None,
                )
            )

    async def mark_acked(self, message_id: str, node_id: str) -> None:
        session = self._session()
        now = _dt_to_str(datetime.now(timezone.utc))
        result = await session.execute(
            select(deliveries_table).where(
                and_(
                    deliveries_table.c.message_id == message_id,
                    deliveries_table.c.node_id == node_id,
                )
            )
        )
        if result.first():
            await session.execute(
                update(deliveries_table)
                .where(
                    and_(
                        deliveries_table.c.message_id == message_id,
                        deliveries_table.c.node_id == node_id,
                    )
                )
                .values(acked_at=now)
            )
        else:
            await session.execute(
                insert(deliveries_table).values(
                    message_id=message_id,
                    node_id=node_id,
                    delivered_at=now,
                    acked_at=now,
                )
            )

    # ── Task ───────────────────────────────────────────────────────────────

    async def save_task(self, task: Task) -> None:
        session = self._session()
        await session.execute(insert(tasks_table).values(**_task_to_row(task)))

    async def get_task(self, task_id: str) -> Task | None:
        session = self._session()
        result = await session.execute(
            select(tasks_table).where(tasks_table.c.id == task_id)
        )
        row = result.first()
        return _row_to_task(row) if row else None

    async def update_task(self, task_id: str, **fields: Any) -> None:
        session = self._session()
        values: dict[str, Any] = {}
        for key, val in fields.items():
            if key == "status":
                values["status"] = val.value if hasattr(val, "value") else val
            elif key == "references":
                values["references_json"] = [
                    r.model_dump() if hasattr(r, "model_dump") else r for r in val
                ]
            elif key in ("created_at", "updated_at") and isinstance(val, datetime):
                values[key] = _dt_to_str(val)
            else:
                values[key] = val
        if values:
            await session.execute(
                update(tasks_table).where(tasks_table.c.id == task_id).values(**values)
            )

    async def query_tasks(self, **filters: Any) -> list[Task]:
        session = self._session()
        stmt = select(tasks_table)
        for key, val in filters.items():
            if key == "assignee":
                stmt = stmt.where(tasks_table.c.assignee == val)
            elif key == "creator":
                stmt = stmt.where(tasks_table.c.creator == val)
            elif key == "status":
                stmt = stmt.where(
                    tasks_table.c.status == (val.value if hasattr(val, "value") else val)
                )
            elif key == "parent_task":
                stmt = stmt.where(tasks_table.c.parent_task == val)
        result = await session.execute(stmt)
        return [_row_to_task(row) for row in result.fetchall()]

    # ── Delegation ───────────────────────────────────────────────────────────────

    async def save_delegation(self, delegation: Delegation) -> None:
        session = self._session()
        await session.execute(
            insert(delegations_table).values(**_delegation_to_row(delegation))
        )

    async def get_delegation(self, delegation_id: str) -> Delegation | None:
        session = self._session()
        result = await session.execute(
            select(delegations_table).where(delegations_table.c.id == delegation_id)
        )
        row = result.first()
        return _row_to_delegation(row) if row else None

    async def delete_delegation(self, delegation_id: str) -> None:
        session = self._session()
        await session.execute(
            delete(delegations_table).where(delegations_table.c.id == delegation_id)
        )

    async def get_delegations_for_delegate(
        self, delegate_id: str, action: str | None = None,
    ) -> list[Delegation]:
        session = self._session()
        stmt = select(delegations_table).where(
            delegations_table.c.delegate == delegate_id
        )
        if action:
            stmt = stmt.where(
                or_(
                    delegations_table.c.action == action,
                    delegations_table.c.action == "*",
                )
            )
        result = await session.execute(stmt)
        return [_row_to_delegation(row) for row in result.fetchall()]

    # ── ConversationTurn ───────────────────────────────────────────────────────

    async def save_conversation_turn(self, turn: ConversationTurn) -> None:
        session = self._session()
        await session.execute(
            insert(conversation_turns_table).values(**_turn_to_row(turn))
        )

    async def query_conversation_turns(
        self,
        node_id: str,
        trigger_message_id: str | None = None,
        limit: int = 50,
    ) -> list[ConversationTurn]:
        session = self._session()
        stmt = select(conversation_turns_table).where(
            conversation_turns_table.c.node_id == node_id
        )
        if trigger_message_id:
            stmt = stmt.where(
                conversation_turns_table.c.trigger_message_id == trigger_message_id
            )
        stmt = stmt.order_by(
            conversation_turns_table.c.timestamp.asc()
        ).limit(limit)
        result = await session.execute(stmt)
        return [_row_to_turn(row) for row in result.fetchall()]

    # ── Event ─────────────────────────────────────────────────────────────────────

    async def append_event(self, event: Event) -> None:
        session = self._session()
        await session.execute(insert(events_table).values(**_event_to_row(event)))

    async def get_events(
        self,
        since: datetime,
        types: list[str] | None = None,
        limit: int = 100,
    ) -> list[Event]:
        session = self._session()
        since_str = _dt_to_str(since)
        stmt = (
            select(events_table)
            .where(events_table.c.timestamp >= since_str)
            .order_by(events_table.c.timestamp.asc())
            .limit(limit)
        )
        if types:
            stmt = stmt.where(events_table.c.type.in_(types))
        result = await session.execute(stmt)
        return [_row_to_event(row) for row in result.fetchall()]
