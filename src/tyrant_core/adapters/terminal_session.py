"""终端 session facade。"""

from __future__ import annotations

import asyncio
import time
from typing import Callable
from uuid import uuid4

import structlog

from tyrant_core.adapters.terminal_process import TerminalProcessSupervisor

logger = structlog.get_logger()


class TerminalOutputBuffer:
    """聚合终端输出，并提供带超时的读取接口。"""

    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._ready = asyncio.Event()

    def append(self, data: str) -> None:
        if not data:
            return
        self._chunks.append(data)
        self._ready.set()

    def pop(self) -> str:
        if not self._chunks:
            return ""
        content = "".join(self._chunks)
        self._chunks.clear()
        self._ready.clear()
        return content

    async def read(self, timeout: float) -> str:
        content = self.pop()
        if content:
            return content
        if timeout <= 0:
            return ""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return ""
        return self.pop()


class TerminalSession:
    """一个交互式终端会话。"""

    def __init__(
        self,
        session_id: str,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        owner_agent_id: str = "",
        supervisor: TerminalProcessSupervisor | None = None,
        output_buffer: TerminalOutputBuffer | None = None,
    ) -> None:
        self.session_id = session_id
        self.command = command
        self.cwd = cwd
        self.env = env
        self.owner_agent_id = owner_agent_id

        self._supervisor = supervisor or TerminalProcessSupervisor(
            command,
            cwd=cwd,
            env=env,
        )
        self._driver = self._supervisor.driver
        self._output_buffer = output_buffer or TerminalOutputBuffer()
        self._master_fd: int | None = None
        self._reader_task: asyncio.Task | None = None
        self._last_activity: float = time.monotonic()
        self._closed = False

    async def start(self) -> None:
        self._master_fd = await self._supervisor.start()
        self._reader_task = asyncio.create_task(
            self._read_loop(),
            name=f"pty-reader-{self.session_id}",
        )
        self._touch()
        logger.info(
            "terminal_session_started",
            session_id=self.session_id,
            command=self.command,
            pid=self._supervisor.pid,
        )

    async def _read_loop(self) -> None:
        master_fd = self._master_fd
        if master_fd is None:
            return

        while not self._closed:
            try:
                data = await self._driver.read(master_fd)
                if not data:
                    break
                self._output_buffer.append(data)
                self._touch()
            except OSError:
                break
            except asyncio.CancelledError:
                break

    async def read(self, timeout: float = 5.0) -> str:
        self._touch()
        return await self._output_buffer.read(timeout)

    async def write(self, data: str) -> None:
        if self._master_fd is None or self._closed:
            raise RuntimeError("Session is closed")
        self._touch()
        await self._driver.write(self._master_fd, data.encode("utf-8"))

    async def send_signal(self, sig: int) -> None:
        await self._supervisor.send_signal(sig)
        self._touch()

    def is_alive(self) -> bool:
        return self._supervisor.is_alive()

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_activity

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        await self._supervisor.shutdown()

        if self._master_fd is not None:
            try:
                self._driver.close_fd(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        logger.info("terminal_session_closed", session_id=self.session_id)

    def _touch(self) -> None:
        self._last_activity = time.monotonic()


TerminalSessionFactory = Callable[..., TerminalSession]


class TerminalSessionManager:
    """管理所有终端会话。"""

    def __init__(
        self,
        *,
        session_factory: TerminalSessionFactory | None = None,
    ) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._session_factory = session_factory or TerminalSession

    async def create_session(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        owner_agent_id: str = "",
    ) -> str:
        session_id = str(uuid4())
        session = self._session_factory(
            session_id=session_id,
            command=command,
            cwd=cwd,
            env=env,
            owner_agent_id=owner_agent_id,
        )
        await session.start()
        self._sessions[session_id] = session
        return session_id

    def get_session(self, session_id: str) -> TerminalSession | None:
        return self._sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.close()

    async def close_sessions_for_agent(self, agent_id: str) -> None:
        to_close = [
            sid for sid, session in self._sessions.items()
            if session.owner_agent_id == agent_id
        ]
        for sid in to_close:
            await self.close_session(sid)
        if to_close:
            logger.info(
                "sessions_closed_for_agent",
                agent_id=agent_id,
                count=len(to_close),
            )

    async def cleanup_idle(self, max_idle_seconds: float) -> int:
        to_close = [
            sid for sid, session in self._sessions.items()
            if session.idle_seconds() > max_idle_seconds or not session.is_alive()
        ]
        for sid in to_close:
            await self.close_session(sid)
        return len(to_close)

    async def close_all(self) -> None:
        for sid in list(self._sessions.keys()):
            await self.close_session(sid)

    @property
    def session_count(self) -> int:
        return len(self._sessions)
