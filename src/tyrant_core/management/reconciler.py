"""Actor 管理态与运行态协调器。"""

from __future__ import annotations

from typing import Protocol

from tyrant_core.models import DesiredState, PresenceStatus
from tyrant_core.store import SqliteStore, Store


class RuntimeSupervisorLike(Protocol):
    async def spawn_actor_runtime(self, actor_id: str) -> dict: ...

    async def stop_actor_runtime(self, actor_id: str) -> None: ...


class ActorReconciler:
    """根据 ActorControl 的期望状态驱动运行态。"""

    def __init__(
        self,
        supervisor: RuntimeSupervisorLike,
        *,
        store: Store | None = None,
    ) -> None:
        self.store = store or SqliteStore()
        self.supervisor = supervisor

    async def reconcile_actor(self, actor_id: str) -> bool:
        async with self.store.transaction():
            control = await self.store.get_actor_control(actor_id)
            if control is None:
                return False
            attachments = await self.store.query_attachments(actor_id=actor_id, limit=100)
            attached = [item for item in attachments if item.detached_at is None]
            online = False
            for attachment in attached:
                presence = await self.store.get_presence(attachment.id)
                if presence is None:
                    continue
                if presence.status in {
                    PresenceStatus.ONLINE,
                    PresenceStatus.BUSY,
                    PresenceStatus.DEGRADED,
                }:
                    online = True
                    break

        if control.desired_state == DesiredState.ACTIVE:
            if attached and online:
                return False
            await self.supervisor.spawn_actor_runtime(actor_id)
            return True

        if control.desired_state in {
            DesiredState.PAUSED,
            DesiredState.DETACHED,
            DesiredState.ARCHIVED,
        }:
            if attached:
                await self.supervisor.stop_actor_runtime(actor_id)
                return True
        return False

    async def reconcile_all(self, *, limit: int = 1000) -> int:
        async with self.store.transaction():
            controls = await self.store.query_actor_controls(limit=limit)
        changed = 0
        for control in controls:
            if await self.reconcile_actor(control.actor_id):
                changed += 1
        return changed
