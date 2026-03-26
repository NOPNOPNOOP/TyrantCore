"""Actor 管理服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from tyrant_core.models import (
    ActivationPolicy,
    Actor,
    ActorControl,
    ActorNotFoundError,
    ActorProfile,
    ActorProfileNotFoundError,
    DesiredState,
    RuntimeKind,
)
from tyrant_core.store import SqliteStore, Store


class ActorManagementService:
    """负责 ActorProfile / ActorControl 的写入与维护。"""

    def __init__(self, store: Store | None = None) -> None:
        self.store = store or SqliteStore()

    async def create_profile(self, spec: dict) -> ActorProfile:
        now = datetime.now(timezone.utc)
        profile = ActorProfile(
            id=spec.get("id", str(uuid4())),
            name=spec["name"],
            instruction=spec.get("instruction", ""),
            toolsets=list(spec.get("toolsets", [])),
            model_ref=spec.get("model_ref"),
            session_mode=spec.get("session_mode", "PERSISTENT"),
            runtime_kind=RuntimeKind(spec.get("runtime_kind", RuntimeKind.LOCAL.value)),
            memory_policy=dict(spec.get("memory_policy", {})),
            bootstrap_policy=dict(spec.get("bootstrap_policy", {})),
            created_at=spec.get("created_at", now),
            archived_at=spec.get("archived_at"),
        )
        async with self.store.transaction():
            await self.store.save_actor_profile(profile)
        return profile

    async def get_profile(self, profile_id: str) -> ActorProfile | None:
        async with self.store.transaction():
            return await self.store.get_actor_profile(profile_id)

    async def query_profiles(
        self,
        *,
        archived: bool | None = None,
        limit: int = 50,
    ) -> list[ActorProfile]:
        async with self.store.transaction():
            return await self.store.query_actor_profiles(
                archived=archived,
                limit=limit,
            )

    async def create_control(self, spec: dict) -> ActorControl:
        now = datetime.now(timezone.utc)
        actor_id = spec["actor_id"]
        manager_actor_id = spec["manager_actor_id"]
        profile_id = spec.get("profile_id")

        async with self.store.transaction():
            await self._require_actor(actor_id)
            await self._require_actor(manager_actor_id)
            if profile_id is not None:
                await self._require_profile(profile_id)

            control = ActorControl(
                id=spec.get("id", str(uuid4())),
                actor_id=actor_id,
                manager_actor_id=manager_actor_id,
                profile_id=profile_id,
                desired_state=DesiredState(
                    spec.get("desired_state", DesiredState.ACTIVE.value)
                ),
                activation_policy=ActivationPolicy(
                    spec.get("activation_policy", ActivationPolicy.AUTO_START.value)
                ),
                runtime_kind=RuntimeKind(
                    spec.get("runtime_kind", RuntimeKind.LOCAL.value)
                ),
                placement=dict(spec.get("placement", {})),
                limits=dict(spec.get("limits", {})),
                updated_at=spec.get("updated_at", now),
            )
            await self.store.save_actor_control(control)
        return control

    async def get_control(self, actor_id: str) -> ActorControl | None:
        async with self.store.transaction():
            return await self.store.get_actor_control(actor_id)

    async def query_controls(
        self,
        *,
        manager_actor_id: str | None = None,
        desired_state: str | None = None,
        runtime_kind: str | None = None,
        limit: int = 50,
    ) -> list[ActorControl]:
        async with self.store.transaction():
            return await self.store.query_actor_controls(
                manager_actor_id=manager_actor_id,
                desired_state=desired_state,
                runtime_kind=runtime_kind,
                limit=limit,
            )

    async def assign_manager(self, actor_id: str, manager_actor_id: str) -> ActorControl:
        now = datetime.now(timezone.utc)
        async with self.store.transaction():
            await self._require_actor(manager_actor_id)
            await self.store.update_actor_control(
                actor_id,
                manager_actor_id=manager_actor_id,
                updated_at=now,
            )
            control = await self.store.get_actor_control(actor_id)
        if control is None:
            raise ActorNotFoundError(actor_id)
        return control

    async def set_profile(self, actor_id: str, profile_id: str | None) -> ActorControl:
        now = datetime.now(timezone.utc)
        async with self.store.transaction():
            if profile_id is not None:
                await self._require_profile(profile_id)
            await self.store.update_actor_control(
                actor_id,
                profile_id=profile_id,
                updated_at=now,
            )
            control = await self.store.get_actor_control(actor_id)
        if control is None:
            raise ActorNotFoundError(actor_id)
        return control

    async def set_desired_state(self, actor_id: str, desired_state: str) -> ActorControl:
        now = datetime.now(timezone.utc)
        async with self.store.transaction():
            await self.store.update_actor_control(
                actor_id,
                desired_state=DesiredState(desired_state),
                updated_at=now,
            )
            control = await self.store.get_actor_control(actor_id)
        if control is None:
            raise ActorNotFoundError(actor_id)
        return control

    async def _require_actor(self, actor_id: str) -> Actor:
        actor = await self.store.get_actor(actor_id)
        if actor is None:
            raise ActorNotFoundError(actor_id)
        return actor

    async def _require_profile(self, profile_id: str) -> ActorProfile:
        profile = await self.store.get_actor_profile(profile_id)
        if profile is None:
            raise ActorProfileNotFoundError(profile_id)
        return profile
