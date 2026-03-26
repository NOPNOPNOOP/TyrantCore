"""本地 runtime 行为注册表。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tyrant_core.models import ActorControl, ActorProfile, ValidationError

from ..actor_runtime import DeliveryHandler

BehaviorFactory = Callable[
    [dict[str, Any], ActorControl, ActorProfile | None, dict[str, Any]],
    DeliveryHandler | None,
]
LocalRuntimeBehaviorInstaller = Callable[["LocalRuntimeBehaviorRegistry"], None]


class LocalRuntimeBehaviorRegistry:
    """把声明式配置解析为本地 runtime handler。"""

    def __init__(self) -> None:
        self._factories: dict[str, BehaviorFactory] = {}

    def register(self, name: str, factory: BehaviorFactory) -> None:
        self._factories[name] = factory

    def install(self, installer: LocalRuntimeBehaviorInstaller) -> None:
        installer(self)

    def list_behaviors(self) -> list[str]:
        return sorted(self._factories)

    def build_handler(
        self,
        actor: dict[str, Any],
        control: ActorControl,
        profile: ActorProfile | None,
    ) -> DeliveryHandler | None:
        spec = self._resolve_spec(actor, profile)
        if spec is None:
            return None
        behavior = spec.get("behavior")
        if not behavior:
            raise ValidationError("local_runtime behavior is missing")
        factory = self._factories.get(behavior)
        if factory is None:
            raise ValidationError(f"unsupported local_runtime behavior: {behavior}")
        return factory(actor, control, profile, spec)

    def _resolve_spec(
        self,
        actor: dict[str, Any],
        profile: ActorProfile | None,
    ) -> dict[str, Any] | None:
        merged: dict[str, Any] = {}
        candidates = []
        if profile is not None:
            candidates.append(profile.bootstrap_policy.get("local_runtime"))
        metadata = dict(actor.get("metadata", {}))
        candidates.append(metadata.get("local_runtime"))
        bootstrap = metadata.get("bootstrap", {})
        if bootstrap is not None and not isinstance(bootstrap, dict):
            raise ValidationError("actor.metadata.bootstrap must be a dict")
        candidates.append(bootstrap.get("local_runtime") if isinstance(bootstrap, dict) else None)

        for candidate in candidates:
            if candidate is None:
                continue
            normalized = self._normalize_spec(candidate)
            merged.update(normalized)
        return merged or None

    def _normalize_spec(self, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return {"behavior": value}
        if isinstance(value, dict):
            return dict(value)
        raise ValidationError("local_runtime config must be a string or dict")
