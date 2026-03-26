"""本地订阅中心。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class SubscriptionHub:
    """按 topic 广播事件。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers[topic].append(queue)
        return queue

    async def publish(self, topic: str, event: dict[str, Any]) -> None:
        for queue in self._subscribers.get(topic, []):
            await queue.put(event)
