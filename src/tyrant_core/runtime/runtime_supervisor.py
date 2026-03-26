"""Actor 运行时监督器。"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone

from tyrant_core.models import (
    ActorControlNotFoundError,
    ActorNotFoundError,
    Presence,
    PresenceStatus,
)
from tyrant_core.store import SqliteStore, Store

from .driver_registry import RuntimeDriverRegistry


class RuntimeSupervisor:
    """把 runtime driver registry 与持久化 attachment/presence 协调起来。"""

    name = "runtime-supervisor"

    def __init__(
        self,
        registry: RuntimeDriverRegistry,
        *,
        store: Store | None = None,
    ) -> None:
        self.store = store or SqliteStore()
        self.registry = registry
        self._active_actor_ids: set[str] = set()

    async def start(self) -> None:
        return None

    async def spawn_actor_runtime(self, actor_id: str) -> dict:
        async with self.store.transaction():
            actor = await self.store.get_actor(actor_id)
            if actor is None:
                raise ActorNotFoundError(actor_id)
            control = await self.store.get_actor_control(actor_id)
            if control is None:
                raise ActorControlNotFoundError(actor_id)
            profile = None
            if control.profile_id is not None:
                profile = await self.store.get_actor_profile(control.profile_id)

        driver = self.registry.require(control.runtime_kind)
        result = await driver.spawn(
            actor_id=actor_id,
            control=control,
            profile=profile,
        )

        async with self.store.transaction():
            await self.store.save_attachment(result.attachment)
            if result.presence is not None:
                await self.store.save_presence(result.presence)
        self._active_actor_ids.add(actor_id)

        return {
            "attachment": result.attachment.model_dump(mode="json"),
            "presence": (
                result.presence.model_dump(mode="json")
                if result.presence is not None
                else None
            ),
        }

    async def stop_actor_runtime(self, actor_id: str) -> None:
        async with self.store.transaction():
            control = await self.store.get_actor_control(actor_id)
            if control is None:
                raise ActorControlNotFoundError(actor_id)
            attachments = await self.store.query_attachments(actor_id=actor_id, limit=100)

        driver = self.registry.require(control.runtime_kind)
        await driver.stop(actor_id=actor_id)

        now = datetime.now(timezone.utc)
        async with self.store.transaction():
            for attachment in attachments:
                updated_attachment = attachment.model_copy(update={"detached_at": now})
                await self.store.save_attachment(updated_attachment)
                presence = await self.store.get_presence(attachment.id)
                if presence is None:
                    continue
                await self.store.save_presence(
                    Presence(
                        attachment_id=presence.attachment_id,
                        actor_id=presence.actor_id,
                        gateway_id=presence.gateway_id,
                        status=PresenceStatus.OFFLINE,
                        leases=presence.leases,
                        last_heartbeat_at=now,
                    )
                )
        self._active_actor_ids.discard(actor_id)

    async def get_runtime_status(self, actor_id: str) -> dict | None:
        async with self.store.transaction():
            control = await self.store.get_actor_control(actor_id)
            if control is None:
                return None
        driver = self.registry.require(control.runtime_kind)
        return await driver.status(actor_id=actor_id)

    async def stop(self) -> None:
        for actor_id in list(self._active_actor_ids):
            with suppress(Exception):
                await self.stop_actor_runtime(actor_id)
