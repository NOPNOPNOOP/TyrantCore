"""运行时驱动协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tyrant_core.models import ActorControl, ActorProfile, Attachment, Presence


@dataclass(slots=True)
class RuntimeSpawnResult:
    attachment: Attachment
    presence: Presence | None = None


class RuntimeDriver(Protocol):
    async def spawn(
        self,
        *,
        actor_id: str,
        control: ActorControl,
        profile: ActorProfile | None = None,
    ) -> RuntimeSpawnResult: ...

    async def stop(self, *, actor_id: str) -> None: ...

    async def status(self, *, actor_id: str) -> dict | None: ...
