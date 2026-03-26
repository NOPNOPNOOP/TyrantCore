"""TERMINAL runtime driver。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tyrant_core.adapters.terminal import TerminalSessionManager
from tyrant_core.gateway.connectors.terminal_bridge import TerminalNodeAdapter
from tyrant_core.models import (
    ActorControl,
    ActorProfile,
    Attachment,
    AttachmentKind,
    Presence,
    PresenceStatus,
    ValidationError,
)

from .runtime_driver import RuntimeDriver, RuntimeSpawnResult

if TYPE_CHECKING:
    from tyrant_core.adapters.terminal import TerminalSession
    from tyrant_core.gateway import GatewayServer

TerminalAdapterFactory = Callable[..., TerminalNodeAdapter]


class TerminalRuntimeDriver(RuntimeDriver):
    """基于 TerminalSession + TerminalNodeAdapter 的 runtime driver。"""

    def __init__(
        self,
        gateway: "GatewayServer" | None = None,
        *,
        session_manager: TerminalSessionManager | None = None,
        adapter_factory: TerminalAdapterFactory | None = None,
        gateway_id: str = "gateway:terminal-runtime",
        aggregation_ms: int = 500,
    ) -> None:
        self.gateway = gateway
        self.session_manager = session_manager or TerminalSessionManager()
        self.adapter_factory = adapter_factory or TerminalNodeAdapter
        self.gateway_id = gateway_id
        self.aggregation_ms = aggregation_ms
        self._tasks: dict[str, asyncio.Task] = {}
        self._session_ids: dict[str, str] = {}
        self._commands: dict[str, str] = {}
        self._controllers: dict[str, str] = {}
        self._thread_ids: dict[str, str | None] = {}
        self._cwds: dict[str, str | None] = {}

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
            raise RuntimeError("TerminalRuntimeDriver is not bound to a GatewayServer")
        if actor_id in self._tasks and not self._tasks[actor_id].done():
            return self._build_spawn_result(
                actor_id=actor_id,
                session_id=self._session_ids[actor_id],
                command=self._commands[actor_id],
                cwd=self._cwds.get(actor_id),
            )

        actor = await self.gateway.get_actor(actor_id)
        spec = self._resolve_spec(actor, control, profile)
        command = spec.get("command")
        if not command:
            raise ValidationError("terminal runtime command is missing")
        cwd = spec.get("cwd")
        env = spec.get("env")
        if env is not None and not isinstance(env, dict):
            raise ValidationError("terminal runtime env must be a dict")
        controller_actor_id = spec.get("controller_actor_id", control.manager_actor_id)
        thread_id = spec.get("thread_id")
        aggregation_ms = int(spec.get("aggregation_ms", self.aggregation_ms))

        session_id = await self.session_manager.create_session(
            str(command),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if isinstance(env, dict) else None,
            owner_agent_id=actor_id,
        )
        session = self.session_manager.get_session(session_id)
        if session is None:
            raise RuntimeError(f"terminal session not found: {session_id}")

        adapter = self.adapter_factory(
            gateway=self.gateway,
            actor_id=actor_id,
            controller_actor_id=str(controller_actor_id),
            session=session,
            thread_id=str(thread_id) if thread_id is not None else None,
            attachment_id=f"attachment:{actor_id}",
            gateway_id=self.gateway_id,
            aggregation_ms=aggregation_ms,
        )
        task = asyncio.create_task(
            adapter.run(),
            name=f"runtime:{actor_id}:terminal",
        )
        task.add_done_callback(
            lambda completed, *, target=actor_id, sid=session_id: self._on_task_done(
                target,
                sid,
                completed,
            )
        )

        self._tasks[actor_id] = task
        self._session_ids[actor_id] = session_id
        self._commands[actor_id] = str(command)
        self._controllers[actor_id] = str(controller_actor_id)
        self._thread_ids[actor_id] = str(thread_id) if thread_id is not None else None
        self._cwds[actor_id] = str(cwd) if cwd is not None else None
        return self._build_spawn_result(
            actor_id=actor_id,
            session_id=session_id,
            command=str(command),
            cwd=str(cwd) if cwd is not None else None,
        )

    async def stop(self, *, actor_id: str) -> None:
        task = self._tasks.pop(actor_id, None)
        session_id = self._session_ids.pop(actor_id, None)
        self._commands.pop(actor_id, None)
        self._controllers.pop(actor_id, None)
        self._thread_ids.pop(actor_id, None)
        self._cwds.pop(actor_id, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if session_id is not None:
            await self.session_manager.close_session(session_id)

    async def status(self, *, actor_id: str) -> dict | None:
        task = self._tasks.get(actor_id)
        if task is None or task.done():
            return None
        session_id = self._session_ids.get(actor_id)
        session = self.session_manager.get_session(session_id) if session_id else None
        return {
            "actor_id": actor_id,
            "driver": "terminal-runtime",
            "session_id": session_id,
            "controller_actor_id": self._controllers.get(actor_id),
            "thread_id": self._thread_ids.get(actor_id),
            "command": self._commands.get(actor_id),
            "cwd": self._cwds.get(actor_id),
            "pid": self._get_session_pid(session),
            "running": session.is_alive() if session is not None else False,
        }

    def _resolve_spec(
        self,
        actor: dict[str, Any],
        control: ActorControl,
        profile: ActorProfile | None,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        candidates = []
        if profile is not None:
            candidates.append(profile.bootstrap_policy.get("terminal_runtime"))
        metadata = dict(actor.get("metadata", {}))
        candidates.append(metadata.get("terminal_runtime"))
        bootstrap = metadata.get("bootstrap", {})
        if bootstrap is not None and not isinstance(bootstrap, dict):
            raise ValidationError("actor.metadata.bootstrap must be a dict")
        candidates.append(
            bootstrap.get("terminal_runtime") if isinstance(bootstrap, dict) else None
        )
        placement = dict(control.placement)
        candidates.append(placement.get("terminal_runtime"))
        candidates.append(placement.get("terminal"))
        if any(
            key in placement
            for key in ("command", "cwd", "env", "thread_id", "controller_actor_id")
        ):
            candidates.append(placement)

        for candidate in candidates:
            if candidate is None:
                continue
            merged.update(self._normalize_spec(candidate))
        return merged

    def _normalize_spec(self, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return {"command": value}
        if isinstance(value, dict):
            return dict(value)
        raise ValidationError("terminal runtime config must be a string or dict")

    def _build_spawn_result(
        self,
        *,
        actor_id: str,
        session_id: str,
        command: str,
        cwd: str | None,
    ) -> RuntimeSpawnResult:
        now = datetime.now(timezone.utc)
        return RuntimeSpawnResult(
            attachment=Attachment(
                id=f"attachment:{actor_id}",
                actor_id=actor_id,
                kind=AttachmentKind.TERMINAL,
                endpoint={
                    "driver": "terminal-runtime",
                    "session_id": session_id,
                    "command": command,
                    "cwd": cwd,
                },
                capabilities=["stdin", "stdout", "signal"],
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

    def _on_task_done(self, actor_id: str, session_id: str, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        asyncio.create_task(self._finalize_task(actor_id, session_id))

    async def _finalize_task(self, actor_id: str, session_id: str) -> None:
        current = self._session_ids.get(actor_id)
        if current != session_id:
            return
        self._tasks.pop(actor_id, None)
        self._session_ids.pop(actor_id, None)
        self._commands.pop(actor_id, None)
        self._controllers.pop(actor_id, None)
        self._thread_ids.pop(actor_id, None)
        self._cwds.pop(actor_id, None)
        await self.session_manager.close_session(session_id)

    def _get_session_pid(self, session: "TerminalSession" | None) -> int | None:
        if session is None:
            return None
        pid = getattr(session, "pid", None)
        if pid is not None:
            return pid
        supervisor = getattr(session, "_supervisor", None)
        return getattr(supervisor, "pid", None)
