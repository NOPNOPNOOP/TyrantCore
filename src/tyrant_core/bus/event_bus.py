"""v2 EventBus。"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from tyrant_core.models import DomainEvent


class EventBus:
    """轻量事件流与订阅中心。"""

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []
        self._subscribers: dict[str, list[asyncio.Queue[DomainEvent]]] = defaultdict(list)

    async def publish(self, event: DomainEvent) -> None:
        self._events.append(event)
        for queue in self._subscribers.get(event.type, []):
            await queue.put(event)
        for queue in self._subscribers.get("*", []):
            await queue.put(event)

    def subscribe(self, event_type: str) -> asyncio.Queue[DomainEvent]:
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue()
        self._subscribers[event_type].append(queue)
        return queue

    def snapshot(self) -> list[DomainEvent]:
        return list(self._events)
