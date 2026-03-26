"""Store 抽象接口。所有持久化操作的契约定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncContextManager, AsyncIterator

from tyrant_core.legacy.models import (
    ConversationTurn,
    Delegation,
    Event,
    Message,
    Node,
    Relation,
    Task,
)


class Store(ABC):
    """持久化层抽象基类。"""

    # ── Node ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def save_node(self, node: Node) -> None: ...

    @abstractmethod
    async def get_node(self, node_id: str) -> Node | None: ...

    @abstractmethod
    async def update_node(self, node_id: str, **fields) -> None: ...

    @abstractmethod
    async def delete_node(self, node_id: str) -> None: ...

    @abstractmethod
    async def list_nodes(self, **filters) -> list[Node]: ...

    # ── Relation ───────────────────────────────────────────────────────────

    @abstractmethod
    async def save_relation(self, relation: Relation) -> None: ...

    @abstractmethod
    async def get_relation(self, relation_id: str) -> Relation | None: ...

    @abstractmethod
    async def delete_relation(self, relation_id: str) -> None: ...

    @abstractmethod
    async def get_relations_for_node(self, node_id: str) -> list[Relation]: ...

    @abstractmethod
    async def get_reports_to(self, node_id: str) -> Relation | None:
        """获取该节点的上级关系。"""
        ...

    @abstractmethod
    async def get_subordinates(self, node_id: str) -> list[Relation]:
        """获取直接下属。"""
        ...

    @abstractmethod
    async def get_subtree_node_ids(self, node_id: str) -> set[str]:
        """获取整棵子树的节点 ID 集合。"""
        ...

    # ── Message ────────────────────────────────────────────────────────────

    @abstractmethod
    async def save_message(self, message: Message) -> None: ...

    @abstractmethod
    async def get_message(self, message_id: str) -> Message | None: ...

    @abstractmethod
    async def get_inbox(self, node_id: str, limit: int = 50) -> list[Message]:
        """按 priority 排序获取收件箱消息。"""
        ...

    @abstractmethod
    async def mark_delivered(self, message_id: str, node_id: str) -> None: ...

    @abstractmethod
    async def mark_acked(self, message_id: str, node_id: str) -> None: ...

    @abstractmethod
    async def query_messages(
        self,
        node_id: str | None = None,
        from_node: str | None = None,
        to_node: str | None = None,
        type: str | None = None,
        limit: int = 50,
    ) -> list[Message]:
        """多条件查询消息。node_id 匹配 from 或 to。按 timestamp DESC。"""
        ...

    # ── Task ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def save_task(self, task: Task) -> None: ...

    @abstractmethod
    async def get_task(self, task_id: str) -> Task | None: ...

    @abstractmethod
    async def update_task(self, task_id: str, **fields) -> None: ...

    @abstractmethod
    async def query_tasks(self, **filters) -> list[Task]: ...

    # ── Delegation ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def save_delegation(self, delegation: Delegation) -> None: ...

    @abstractmethod
    async def get_delegation(self, delegation_id: str) -> Delegation | None: ...

    @abstractmethod
    async def delete_delegation(self, delegation_id: str) -> None: ...

    @abstractmethod
    async def get_delegations_for_delegate(
        self, delegate_id: str, action: str | None = None,
    ) -> list[Delegation]:
        """获取某节点收到的所有委托，可按 action 过滤。"""
        ...

    # ── ConversationTurn ───────────────────────────────────────────────────────

    @abstractmethod
    async def save_conversation_turn(self, turn: ConversationTurn) -> None: ...

    @abstractmethod
    async def query_conversation_turns(
        self,
        node_id: str,
        trigger_message_id: str | None = None,
        limit: int = 50,
    ) -> list[ConversationTurn]:
        """查询节点的对话历史。按 timestamp ASC。"""
        ...

    # ── Event ───────────────────────────────────────────────────────────────────

    @abstractmethod
    async def append_event(self, event: Event) -> None: ...

    @abstractmethod
    async def get_events(
        self,
        since: datetime,
        types: list[str] | None = None,
        limit: int = 100,
    ) -> list[Event]: ...

    # ── Transaction ────────────────────────────────────────────────────────

    @abstractmethod
    def transaction(self) -> AsyncContextManager:
        """async with store.transaction(): ..."""
        ...

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """创建表、索引等。"""
        ...

    @abstractmethod
    async def close(self) -> None: ...
