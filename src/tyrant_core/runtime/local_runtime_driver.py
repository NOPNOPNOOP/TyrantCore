"""默认本地 RuntimeDriver 实现。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from tyrant_core.models import (
    ActorControl,
    ActorProfile,
    Attachment,
    AttachmentKind,
    Presence,
    PresenceStatus,
)

from .actor_runtime import DeliveryHandler
from .local_runtime import LocalRuntimeBehaviorRegistry
from .runtime_driver import RuntimeDriver, RuntimeSpawnResult

if TYPE_CHECKING:
    from tyrant_core.gateway import GatewayServer

LocalHandlerFactory = Callable[
    [str, ActorControl, ActorProfile | None],
    DeliveryHandler | None,
]


class LocalRuntimeDriver(RuntimeDriver):
    """基于 ActorRuntime 的默认本地驱动。"""

    def __init__(
        self,
        gateway: "GatewayServer" | None = None,
        *,
        handler_factory: LocalHandlerFactory | None = None,
        behavior_registry: LocalRuntimeBehaviorRegistry | None = None,
        poll_interval: float = 1.0,
        lease_seconds: int = 30,
        batch_size: int = 1,
        gateway_id: str = "gateway:local-runtime",
    ) -> None:
        self.gateway = gateway
        self.handler_factory = handler_factory
        self.behavior_registry = behavior_registry or LocalRuntimeBehaviorRegistry()
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.batch_size = batch_size
        self.gateway_id = gateway_id
        self._tasks: dict[str, asyncio.Task] = {}
        self._modes: dict[str, str] = {}
        self._last_errors: dict[str, str | None] = {}

    def bind_gateway(self, gateway: "GatewayServer") -> None:
        self.gateway = gateway

    async def spawn(
        self,
        *,
        actor_id: str,
        control: ActorControl,
        profile: ActorProfile | None = None,
    ) -> RuntimeSpawnResult:
        if self.gateway is None:
            raise RuntimeError("LocalRuntimeDriver is not bound to a GatewayServer")
        if actor_id in self._tasks and not self._tasks[actor_id].done():
            return self._build_spawn_result(
                actor_id,
                mode=self._modes.get(actor_id, "idle"),
            )

        handler = None
        if self.handler_factory is not None:
            handler = self.handler_factory(actor_id, control, profile)
        if handler is None:
            actor = await self.gateway.get_actor(actor_id)
            handler = self.behavior_registry.build_handler(actor, control, profile)

        if handler is None:
            mode = "idle"
            task = asyncio.create_task(self._idle_loop(), name=f"runtime:{actor_id}:idle")
        else:
            mode = "consumer"
            task = asyncio.create_task(
                self._consume_loop(actor_id, handler),
                name=f"runtime:{actor_id}:consumer",
            )

        self._tasks[actor_id] = task
        self._modes[actor_id] = mode
        self._last_errors[actor_id] = None
        return self._build_spawn_result(actor_id, mode=mode)

    async def stop(self, *, actor_id: str) -> None:
        task = self._tasks.pop(actor_id, None)
        self._modes.pop(actor_id, None)
        self._last_errors.pop(actor_id, None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def status(self, *, actor_id: str) -> dict | None:
        task = self._tasks.get(actor_id)
        if task is None or task.done():
            return None
        return {
            "actor_id": actor_id,
            "driver": "local-runtime",
            "mode": self._modes.get(actor_id, "idle"),
            "consumer_id": f"runtime:{actor_id}",
            "running": True,
            "last_error": self._last_errors.get(actor_id),
        }

    def _build_spawn_result(self, actor_id: str, *, mode: str) -> RuntimeSpawnResult:
        now = datetime.now(timezone.utc)
        return RuntimeSpawnResult(
            attachment=Attachment(
                id=f"attachment:{actor_id}",
                actor_id=actor_id,
                kind=AttachmentKind.LOCAL_RUNTIME,
                endpoint={
                    "driver": "local-runtime",
                    "mode": mode,
                },
                capabilities=["consume"] if mode == "consumer" else [],
                created_at=now,
            ),
            presence=Presence(
                attachment_id=f"attachment:{actor_id}",
                actor_id=actor_id,
                gateway_id=self.gateway_id,
                status=PresenceStatus.ONLINE,
                leases=0,
                last_heartbeat_at=now,
            ),
        )

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)

    async def _consume_loop(self, actor_id: str, handler: DeliveryHandler) -> None:
        assert self.gateway is not None
        runtime = self._build_runtime()
        consumer_id = f"runtime:{actor_id}"
        while True:
            try:
                processed = await runtime.consume_once(
                    actor_id=actor_id,
                    consumer_id=consumer_id,
                    handler=handler,
                    limit=self.batch_size,
                    lease_seconds=self.lease_seconds,
                )
                self._last_errors[actor_id] = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_errors[actor_id] = str(exc)
                processed = 0
            if processed:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(self.poll_interval)

    def _build_runtime(self):
        assert self.gateway is not None
        from .actor_runtime import ActorRuntime

        return ActorRuntime(self.gateway)
