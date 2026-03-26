"""执行管理器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4


@dataclass(slots=True)
class ExecutionState:
    """一次执行上下文。"""

    id: str
    actor_id: str
    thread_id: str | None
    status: str
    started_at: datetime


class ExecutionManager:
    """管理运行中的执行实例。"""

    def __init__(self) -> None:
        self._executions: dict[str, ExecutionState] = {}

    def start(self, actor_id: str, thread_id: str | None = None) -> ExecutionState:
        execution = ExecutionState(
            id=str(uuid4()),
            actor_id=actor_id,
            thread_id=thread_id,
            status="RUNNING",
            started_at=datetime.now(timezone.utc),
        )
        self._executions[execution.id] = execution
        return execution
