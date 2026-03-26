"""Actor 管理态后台协调 worker。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from .reconciler import ActorReconciler


class ActorReconcileWorker:
    """把 ActorReconciler 包装为可常驻运行的后台 worker。"""

    def __init__(
        self,
        reconciler: ActorReconciler,
        *,
        poll_interval: float = 1.0,
        limit: int = 1000,
        name: str = "reconcile-worker",
    ) -> None:
        self.reconciler = reconciler
        self.poll_interval = poll_interval
        self.limit = limit
        self.name = name
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def tick(self) -> int:
        """执行一次全量 reconcile。"""
        return await self.reconciler.reconcile_all(limit=self.limit)

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run_loop(), name=self.name)

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                changed = await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                changed = 0
            if changed:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(self.poll_interval)
