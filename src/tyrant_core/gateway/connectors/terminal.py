"""Terminal connector。"""

from __future__ import annotations

import asyncio

from tyrant_core.adapters.terminal import TerminalSessionManager
from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors.terminal_bridge import TerminalNodeAdapter


class TerminalConnector:
    name = "terminal"

    def __init__(
        self,
        gateway: GatewayServer,
        *,
        session_manager: TerminalSessionManager | None = None,
        gateway_id: str = "gateway:local",
    ) -> None:
        self.gateway = gateway
        self.session_manager = session_manager or TerminalSessionManager()
        self.gateway_id = gateway_id
        self._tasks: dict[str, asyncio.Task] = {}
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        await self.session_manager.close_all()
        self._started = False

    async def spawn(
        self,
        *,
        actor_id: str,
        controller_actor_id: str,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        thread_id: str | None = None,
    ) -> dict:
        if not self._started:
            raise RuntimeError("TerminalConnector 尚未启动")

        actors = await self.gateway.list_actors(id=actor_id)
        if not actors:
            await self.gateway.create_actor({
                "id": actor_id,
                "kind": "TERMINAL",
                "name": actor_id,
                "slug": actor_id,
                "bypass_policy": True,
            })

        session_id = await self.session_manager.create_session(
            command=command,
            cwd=cwd,
            env=env,
            owner_agent_id=actor_id,
        )
        session = self.session_manager.get_session(session_id)
        if session is None:
            raise RuntimeError(f"terminal session not found: {session_id}")

        adapter = TerminalNodeAdapter(
            gateway=self.gateway,
            actor_id=actor_id,
            controller_actor_id=controller_actor_id,
            session=session,
            thread_id=thread_id,
            gateway_id=self.gateway_id,
        )
        task = asyncio.create_task(
            adapter.run(),
            name=f"terminal-connector:{actor_id}:{session_id}",
        )
        self._tasks[session_id] = task
        return {
            "session_id": session_id,
            "actor_id": actor_id,
            "attachment_id": adapter.attachment_id,
            "thread_id": thread_id,
        }

    async def stop_session(self, session_id: str) -> None:
        task = self._tasks.pop(session_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.session_manager.close_session(session_id)
