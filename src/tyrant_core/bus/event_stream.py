"""v2 事件流。"""

from __future__ import annotations

from collections import deque
from typing import Any


class EventStream:
    """最小追加式事件流。"""

    def __init__(self, maxlen: int = 1024) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def append(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._events)
