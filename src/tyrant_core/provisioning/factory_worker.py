"""默认创建工厂的后台 worker。"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from .base import ProvisioningQueueProcessorLike


class ProvisioningFactoryWorker:
    """把创建队列处理器包装为可常驻运行的后台 worker。"""

    def __init__(
        self,
        queue_processor: ProvisioningQueueProcessorLike,
        *,
        owner_actor_id: str,
        executor_actor_id: str | None = None,
        poll_interval: float = 1.0,
        auto_reconcile: bool = True,
        scan_limit: int = 20,
        name: str | None = None,
    ) -> None:
        self.queue_processor = queue_processor
        self.agent_factory = queue_processor
        self.owner_actor_id = owner_actor_id
        self.executor_actor_id = executor_actor_id or owner_actor_id
        self.poll_interval = poll_interval
        self.auto_reconcile = auto_reconcile
        self.scan_limit = scan_limit
        self.name = name or f"factory-worker:{owner_actor_id}"
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def tick(self) -> dict | None:
        """处理一条创建工单；没有可处理工单则返回 None。"""
        return await self.queue_processor.process_next(
            owner_actor_id=self.owner_actor_id,
            executor_actor_id=self.executor_actor_id,
            auto_reconcile=self.auto_reconcile,
            scan_limit=self.scan_limit,
        )

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
                result = await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                result = None
            if result is None:
                await asyncio.sleep(self.poll_interval)
            else:
                await asyncio.sleep(0)
