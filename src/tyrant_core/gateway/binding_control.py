"""Gateway binding 子控制器。"""

from __future__ import annotations

from typing import Any

from tyrant_core.gateway.binding_resolver import BindingMatch, BindingResolver


def _dump_binding(match: BindingMatch) -> dict[str, Any]:
    return {
        "binding_id": match.binding_id,
        "actor_id": match.actor_id,
        "surface": match.surface,
        "external_key": match.external_key,
        "account_id": match.account_id,
        "thread_policy": match.thread_policy,
        "fixed_thread_id": match.fixed_thread_id,
        "priority": match.priority,
        "metadata": dict(match.metadata),
    }


class GatewayBindingControl:
    """负责 binding 注册、解析与查询。"""

    def __init__(self, bindings: BindingResolver) -> None:
        self.bindings = bindings

    async def register_binding(self, spec: dict[str, Any]) -> dict[str, Any]:
        binding = await self.bindings.register(
            BindingMatch(
                actor_id=spec["actor_id"],
                surface=spec["surface"],
                external_key=spec.get("external_key", spec.get("peer")),
                binding_id=spec.get("id"),
                account_id=spec.get("account_id"),
                thread_policy=spec.get("thread_policy", "REUSE"),
                fixed_thread_id=spec.get("fixed_thread_id"),
                priority=spec.get("priority", 0),
                metadata=dict(spec.get("metadata", {})),
            )
        )
        return binding.model_dump(mode="json")

    async def query_bindings(
        self,
        *,
        surface: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        matches = await self.bindings.query(
            surface=surface,
            actor_id=actor_id,
            limit=limit,
        )
        return [_dump_binding(match) for match in matches]

    async def resolve_binding(
        self,
        *,
        surface: str,
        external_key: str,
        account_id: str | None = None,
    ) -> dict[str, Any] | None:
        match = await self.bindings.resolve(
            surface=surface,
            external_key=external_key,
            account_id=account_id,
        )
        if match is None:
            return None
        return _dump_binding(match)
