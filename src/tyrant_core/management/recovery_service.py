"""启动期 Actor 恢复服务。"""

from __future__ import annotations

from .reconciler import ActorReconciler


class ActorRecoveryService:
    """在 gateway 启动时执行一次全量恢复。"""

    def __init__(
        self,
        reconciler: ActorReconciler,
        *,
        limit: int = 1000,
        strict: bool = False,
        name: str = "actor-recovery",
    ) -> None:
        self.reconciler = reconciler
        self.limit = limit
        self.strict = strict
        self.name = name
        self.recovered_count = 0
        self.failures: list[dict[str, str]] = []
        self.last_error: str | None = None

    async def start(self) -> None:
        self.recovered_count = 0
        self.failures = []
        self.last_error = None
        async with self.reconciler.store.transaction():
            controls = await self.reconciler.store.query_actor_controls(limit=self.limit)

        for control in controls:
            try:
                changed = await self.reconciler.reconcile_actor(control.actor_id)
                if changed:
                    self.recovered_count += 1
            except Exception as exc:
                self.last_error = str(exc)
                self.failures.append({
                    "actor_id": control.actor_id,
                    "error": str(exc),
                })
                if self.strict:
                    raise

    async def stop(self) -> None:
        return None
