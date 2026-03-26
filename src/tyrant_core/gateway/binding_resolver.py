"""Binding 解析器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tyrant_core.models import Binding, BindingThreadPolicy
from tyrant_core.store import Store


@dataclass(slots=True)
class BindingMatch:
    """一次 binding 解析结果。"""

    actor_id: str
    surface: str
    external_key: str | None
    binding_id: str | None = None
    account_id: str | None = None
    thread_policy: str = "REUSE"
    fixed_thread_id: str | None = None
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class BindingResolver:
    """基于 Store 的 binding resolver。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def register(self, match: BindingMatch) -> Binding:
        binding = Binding(
            id=match.binding_id or str(uuid4()),
            surface=match.surface,
            account_id=match.account_id,
            peer=match.external_key,
            actor_id=match.actor_id,
            thread_policy=BindingThreadPolicy(match.thread_policy),
            fixed_thread_id=match.fixed_thread_id,
            priority=match.priority,
            metadata=dict(match.metadata),
            created_at=datetime.now(timezone.utc),
            disabled_at=None,
        )
        async with self.store.transaction():
            await self.store.save_binding(binding)
        return binding

    async def resolve(
        self,
        surface: str,
        external_key: str,
        *,
        account_id: str | None = None,
    ) -> BindingMatch | None:
        async with self.store.transaction():
            binding = await self.store.resolve_binding(
                surface=surface,
                account_id=account_id,
                peer=external_key,
            )
        if binding is None:
            return None
        return BindingMatch(
            actor_id=binding.actor_id,
            surface=binding.surface,
            external_key=binding.peer or external_key,
            binding_id=binding.id,
            account_id=binding.account_id,
            thread_policy=binding.thread_policy.value,
            fixed_thread_id=binding.fixed_thread_id,
            priority=binding.priority,
            metadata=dict(binding.metadata),
        )

    async def query(
        self,
        *,
        surface: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[BindingMatch]:
        async with self.store.transaction():
            bindings = await self.store.query_bindings(
                surface=surface,
                actor_id=actor_id,
                limit=limit,
            )
        return [
            BindingMatch(
                actor_id=binding.actor_id,
                surface=binding.surface,
                external_key=binding.peer,
                binding_id=binding.id,
                account_id=binding.account_id,
                thread_policy=binding.thread_policy.value,
                fixed_thread_id=binding.fixed_thread_id,
                priority=binding.priority,
                metadata=dict(binding.metadata),
            )
            for binding in bindings
        ]
