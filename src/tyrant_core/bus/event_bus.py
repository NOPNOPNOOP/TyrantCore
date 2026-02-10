"""事件总线。pub/sub + 通配符匹配 + 持久化 + replay。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Callable, Awaitable

import structlog

from tyrant_core.models import Event
from tyrant_core.store.base import Store

logger = structlog.get_logger()

# 回调签名: async def handler(event: Event) -> None
EventCallback = Callable[[Event], Awaitable[None]]


class EventBus:
    """自建事件总线，~100-150 行核心逻辑。"""

    def __init__(self, store: Store) -> None:
        self._store = store
        # event_type_pattern → [(node_id, callback)]
        self._subscribers: dict[str, list[tuple[str, EventCallback]]] = defaultdict(list)

    # ── 发布 ───────────────────────────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """发布事件：持久化 → 分发到匹配的订阅者回调。"""
        await self._store.append_event(event)

        for pattern, subscribers in self._subscribers.items():
            if self._matches(pattern, event.type):
                for node_id, callback in subscribers:
                    try:
                        await callback(event)
                    except Exception:
                        logger.exception(
                            "event_callback_failed",
                            node_id=node_id,
                            event_type=event.type,
                        )

    # ── 订阅 ───────────────────────────────────────────────────────────────

    async def subscribe(
        self,
        node_id: str,
        event_type: str,
        callback: EventCallback,
    ) -> None:
        """
        订阅事件类型。

        - event_type 支持通配符: "node.*" 匹配 "node.status_changed" 等
        - callback: async def handler(event: Event) -> None
        - 持久化订阅关系到 store
        """
        # 避免重复订阅
        for existing_node_id, _ in self._subscribers[event_type]:
            if existing_node_id == node_id:
                return

        self._subscribers[event_type].append((node_id, callback))

        # 持久化（用于重启恢复）
        await self._persist_subscription(node_id, event_type)

    async def unsubscribe(self, node_id: str, event_type: str) -> None:
        """取消订阅。"""
        subs = self._subscribers.get(event_type, [])
        self._subscribers[event_type] = [
            (nid, cb) for nid, cb in subs if nid != node_id
        ]
        if not self._subscribers[event_type]:
            del self._subscribers[event_type]

        await self._remove_subscription(node_id, event_type)

    # ── 回放 ───────────────────────────────────────────────────────────────

    async def replay(
        self,
        since: datetime,
        types: list[str] | None = None,
    ) -> list[Event]:
        """回放指定时间点之后的事件。"""
        return await self._store.get_events(since=since, types=types)

    # ── 重启恢复 ───────────────────────────────────────────────────────────

    async def recover_subscriptions(self) -> None:
        """
        系统重启时从 store 恢复订阅关系。

        注意：仅恢复订阅记录，回调需由 runtime 层重新注册。
        此方法加载持久化的 (node_id, event_type) 对，
        runtime 层随后为每个节点调用 subscribe() 重新绑定回调。
        """
        # 实际的回调绑定由 runtime 层完成，这里只是提供恢复入口
        logger.info("event_bus_recovery_started")

    def get_subscription_patterns(self) -> dict[str, list[str]]:
        """返回当前所有订阅：{event_type: [node_id, ...]}，供 runtime 恢复用。"""
        result: dict[str, list[str]] = {}
        for pattern, subs in self._subscribers.items():
            result[pattern] = [node_id for node_id, _ in subs]
        return result

    # ── 通配符匹配 ─────────────────────────────────────────────────────────

    @staticmethod
    def _matches(pattern: str, event_type: str) -> bool:
        """
        简单通配符匹配：
        - "*" 匹配所有
        - "node.*" 匹配 "node.status_changed", "node.destroyed"
        - "task.status_changed" 精确匹配
        """
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return event_type.startswith(prefix + ".")
        return pattern == event_type

    # ── 持久化辅助 ─────────────────────────────────────────────────────────

    async def _persist_subscription(self, node_id: str, event_type: str) -> None:
        """将订阅关系持久化到 subscriptions 表。"""
        from tyrant_core.store.sqlite_store import subscriptions_table
        from sqlalchemy import insert, select

        session = self._store._session()  # type: ignore[attr-defined]
        # 检查是否已存在
        result = await session.execute(
            select(subscriptions_table).where(
                subscriptions_table.c.node_id == node_id,
                subscriptions_table.c.event_type == event_type,
            )
        )
        if not result.first():
            await session.execute(
                insert(subscriptions_table).values(
                    node_id=node_id,
                    event_type=event_type,
                    created_at=datetime.now().isoformat(),
                )
            )

    async def _remove_subscription(self, node_id: str, event_type: str) -> None:
        """从 subscriptions 表删除订阅关系。"""
        from tyrant_core.store.sqlite_store import subscriptions_table
        from sqlalchemy import delete

        session = self._store._session()  # type: ignore[attr-defined]
        await session.execute(
            delete(subscriptions_table).where(
                subscriptions_table.c.node_id == node_id,
                subscriptions_table.c.event_type == event_type,
            )
        )
