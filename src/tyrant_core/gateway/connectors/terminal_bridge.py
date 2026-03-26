"""终端 attachment 与 Gateway 之间的桥接逻辑。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tyrant_core.adapters.terminal import TerminalSession
from tyrant_core.gateway.connectors.terminal_lifecycle import TerminalAttachmentLifecycle
from tyrant_core.gateway.connectors.terminal_pumps import (
    TerminalStdinPump,
    TerminalStdoutPump,
)

if TYPE_CHECKING:
    from tyrant_core.gateway.server import GatewayServer


class TerminalNodeAdapter:
    """把交互式终端会话挂到 Gateway 控制平面。"""

    def __init__(
        self,
        *,
        gateway: GatewayServer,
        actor_id: str,
        controller_actor_id: str,
        session: TerminalSession,
        thread_id: str | None = None,
        attachment_id: str | None = None,
        gateway_id: str = "gateway:local",
        aggregation_ms: int = 500,
    ) -> None:
        self.gateway = gateway
        self.actor_id = actor_id
        self.controller_actor_id = controller_actor_id
        self.session = session
        self.thread_id = thread_id
        self.attachment_id = attachment_id or f"terminal:{session.session_id}"
        self.gateway_id = gateway_id
        self.aggregation_ms = aggregation_ms
        self.consumer_id = f"terminal-consumer:{actor_id}"
        self.lifecycle = TerminalAttachmentLifecycle(
            gateway=self.gateway,
            actor_id=self.actor_id,
            attachment_id=self.attachment_id,
            session=self.session,
            gateway_id=self.gateway_id,
        )
        self.stdout_pump = TerminalStdoutPump(
            gateway=self.gateway,
            actor_id=self.actor_id,
            controller_actor_id=self.controller_actor_id,
            session=self.session,
            aggregation_ms=self.aggregation_ms,
        )
        self.stdin_pump = TerminalStdinPump(
            gateway=self.gateway,
            actor_id=self.actor_id,
            consumer_id=self.consumer_id,
            session=self.session,
        )

    async def attach(self) -> None:
        """注册 terminal attachment 和 presence。"""
        await self.lifecycle.attach()

    async def run(self) -> None:
        """并行同步 stdout 和 stdin。"""
        await self.attach()
        try:
            stdout_thread_id, _ = await asyncio.gather(
                self.stdout_pump.run(thread_id=self.thread_id),
                self.stdin_pump.run(),
            )
            self.thread_id = stdout_thread_id
        finally:
            await self.lifecycle.detach()

    async def _stdout_loop(self) -> None:
        self.thread_id = await self.stdout_pump.run(thread_id=self.thread_id)

    async def _stdin_loop(self) -> None:
        await self.stdin_pump.run()
