"""消息总线。路由检查 + from_node 填充 + 多播 + Queue inbox + ACK。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4

import structlog

from tyrant_core.models import Event, Message, Priority
from tyrant_core.store.base import Store
from tyrant_core.bus.event_bus import EventBus

logger = structlog.get_logger()


class MessageBus:
    """中心化消息路由。"""

    def __init__(self, store: Store, event_bus: EventBus) -> None:
        self._store = store
        self._event_bus = event_bus
        # node_id → asyncio.Queue[Message]
        self._inboxes: dict[str, asyncio.Queue[Message]] = {}

    # ── 发送 ───────────────────────────────────────────────────────────────

    async def send(self, message: Message, sender_id: str) -> list[str]:
        """
        发送消息。

        - 系统自动填充 from_node = sender_id
        - 检查路由权限
        - 多播展开
        - 投递到每个接收者的 inbox
        - 返回实际投递的 message_id 列表
        """
        # 填充 from_node（不可伪造）
        message = message.model_copy(update={"from_node": sender_id})

        delivered_ids: list[str] = []

        if isinstance(message.to, list):
            # 多播展开
            for to_id in message.to:
                if not await self._check_route(sender_id, to_id):
                    logger.warning(
                        "routing_denied",
                        from_node=sender_id,
                        to_node=to_id,
                    )
                    continue
                single = message.model_copy(
                    update={"to": to_id, "id": str(uuid4())}
                )
                await self._deliver(single, to_id)
                delivered_ids.append(single.id)
        else:
            to_id = message.to
            if not await self._check_route(sender_id, to_id):
                from tyrant_core.models import RoutingError
                raise RoutingError(
                    f"No route from {sender_id} to {to_id}"
                )
            await self._deliver(message, to_id)
            delivered_ids.append(message.id)

        return delivered_ids

    # ── 收件箱 ─────────────────────────────────────────────────────────────

    async def get_inbox(self, node_id: str, limit: int = 50) -> list[Message]:
        """获取节点的待处理消息，按 priority ASC, timestamp ASC 排序。"""
        return await self._store.get_inbox(node_id, limit=limit)

    async def subscribe_inbox(self, node_id: str) -> AsyncIterator[Message]:
        """异步迭代器，实时监听新消息（用于 Agent runtime）。"""
        if node_id not in self._inboxes:
            self._inboxes[node_id] = asyncio.Queue()
        queue = self._inboxes[node_id]

        while True:
            message = await queue.get()
            yield message

    # ── ACK ────────────────────────────────────────────────────────────────

    async def ack(self, message_id: str, node_id: str) -> None:
        """确认消息已处理。"""
        await self._store.mark_acked(message_id, node_id)

    # ── 内部方法 ───────────────────────────────────────────────────────────

    async def _check_route(self, from_id: str, to_id: str) -> bool:
        """
        路由检查：子树可达模型。

        - 系统消息（from_id == "system"）始终允许
        - 有直接关系（REPORTS_TO 或 CONNECTED）则允许
        - 在同一 REPORTS_TO 链上（祖先-后代）则允许
        """
        if from_id == "system":
            return True

        # 快速路径：直接关系
        relations = await self._store.get_relations_for_node(from_id)
        for rel in relations:
            other = rel.node_b if rel.node_a == from_id else rel.node_a
            if other == to_id:
                return True

        # 子树可达：检查祖先-后代链
        if await self._is_ancestor(from_id, to_id):
            return True
        if await self._is_ancestor(to_id, from_id):
            return True

        return False

    async def _is_ancestor(
        self, ancestor_id: str, descendant_id: str
    ) -> bool:
        """沿 REPORTS_TO 向上走，检查 ancestor_id 是否是 descendant_id 的祖先。"""
        current = descendant_id
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            rel = await self._store.get_reports_to(current)
            if rel is None:
                return False
            if rel.node_a == ancestor_id:
                return True
            current = rel.node_a
        return False

    async def _deliver(self, message: Message, to_id: str) -> None:
        """持久化消息并投递到内存 inbox。"""
        # 确保 message.to 是单个接收者字符串
        if isinstance(message.to, list):
            message = message.model_copy(update={"to": to_id})

        await self._store.save_message(message)
        await self._store.mark_delivered(message.id, to_id)

        # 投递到内存 queue
        if to_id not in self._inboxes:
            self._inboxes[to_id] = asyncio.Queue()
        await self._inboxes[to_id].put(message)

        # 发布消息投递事件
        await self._event_bus.publish(Event(
            id=str(uuid4()),
            type="message.delivered",
            timestamp=datetime.now(timezone.utc),
            source="system",
            data={
                "message_id": message.id,
                "from_node": message.from_node,
                "to_node": to_id,
            },
        ))

    # ── 恢复 ───────────────────────────────────────────────────────────────

    async def recover_inboxes(self, node_ids: list[str]) -> None:
        """系统重启时从 SQLite 恢复未 ACK 的消息到 queue。"""
        for node_id in node_ids:
            messages = await self._store.get_inbox(node_id)
            if node_id not in self._inboxes:
                self._inboxes[node_id] = asyncio.Queue()
            for msg in messages:
                await self._inboxes[node_id].put(msg)
            if messages:
                logger.info(
                    "inbox_recovered",
                    node_id=node_id,
                    count=len(messages),
                )
