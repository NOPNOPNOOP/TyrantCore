"""trace 事件对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class TraceEvent:
    """runtime trace 事件。"""

    kind: str
    actor_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class TraceRecorder:
    """最小 trace 记录器。"""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def record(self, event: TraceEvent) -> None:
        self._events.append(event)

    def snapshot(self) -> list[TraceEvent]:
        return list(self._events)
