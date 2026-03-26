"""终端会话管理。基于 pty 的交互式进程管理。"""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import time
from uuid import uuid4

import structlog

logger = structlog.get_logger()


class TerminalSession:
    """一个交互式终端会话，基于 pty。

    每个 session 管理一个子进程，通过 pty 实现 stdin/stdout 双向通信。
    """

    def __init__(
        self,
        session_id: str,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        owner_agent_id: str = "",
    ) -> None:
        self.session_id = session_id
        self.command = command
        self.cwd = cwd
        self.env = env
        self.owner_agent_id = owner_agent_id

        self._process: asyncio.subprocess.Process | None = None
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._buffer: str = ""
        self._reader_task: asyncio.Task | None = None
        self._last_activity: float = time.monotonic()
        self._closed: bool = False

    async def start(self) -> None:
        """启动子进程（通过 pty）。"""
        master_fd, slave_fd = os.openpty()
        self._master_fd = master_fd
        self._slave_fd = slave_fd

        # 构建环境变量
        proc_env = os.environ.copy()
        if self.env:
            proc_env.update(self.env)
        # 设置 TERM 以支持交互式程序
        proc_env.setdefault("TERM", "xterm-256color")

        self._process = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", self.command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.cwd,
            env=proc_env,
        )
        # slave_fd 已被子进程继承，父进程关闭
        os.close(slave_fd)
        self._slave_fd = None

        # 启动后台读取协程（master_fd 保持阻塞模式，在 executor 中运行）
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"pty-reader-{self.session_id}"
        )
        self._last_activity = time.monotonic()
        logger.info(
            "terminal_session_started",
            session_id=self.session_id,
            command=self.command,
            pid=self._process.pid,
        )

    async def _read_loop(self) -> None:
        """持续从 master_fd 读取输出到 buffer。"""
        loop = asyncio.get_event_loop()
        master_fd = self._master_fd
        if master_fd is None:
            return

        while not self._closed:
            try:
                data = await loop.run_in_executor(
                    None, self._blocking_read, master_fd
                )
                if data:
                    self._buffer += data
                    self._last_activity = time.monotonic()
                else:
                    # EOF
                    break
            except OSError:
                break
            except asyncio.CancelledError:
                break

    @staticmethod
    def _blocking_read(fd: int, size: int = 4096) -> str:
        """阻塞读取 fd（在 executor 中运行）。"""
        try:
            raw = os.read(fd, size)
            if not raw:
                return ""
            return raw.decode("utf-8", errors="replace")
        except OSError as e:
            if e.errno in (errno.EIO, errno.EBADF, errno.EAGAIN):
                return ""
            raise

    async def read(self, timeout: float = 5.0) -> str:
        """读取当前 buffer 内容。

        等待模式：最多等 timeout 秒，有输出就返回，超时返回空。
        """
        self._last_activity = time.monotonic()

        # 如果 buffer 已有内容，直接返回
        if self._buffer:
            content = self._buffer
            self._buffer = ""
            return content

        # 否则等待新输出
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if self._buffer:
                content = self._buffer
                self._buffer = ""
                return content

        return ""

    async def write(self, data: str) -> None:
        """写入数据到 stdin。"""
        if self._master_fd is None or self._closed:
            raise RuntimeError("Session is closed")
        self._last_activity = time.monotonic()
        loop = asyncio.get_event_loop()
        encoded = data.encode("utf-8")
        await loop.run_in_executor(None, os.write, self._master_fd, encoded)

    async def send_signal(self, sig: int) -> None:
        """向子进程发送信号。"""
        if self._process and self._process.returncode is None:
            self._process.send_signal(sig)
            self._last_activity = time.monotonic()

    def is_alive(self) -> bool:
        """子进程是否仍在运行。"""
        if self._process is None:
            return False
        return self._process.returncode is None

    def idle_seconds(self) -> float:
        """距离上次活动的秒数。"""
        return time.monotonic() - self._last_activity

    async def close(self) -> None:
        """关闭会话：kill 进程 + 清理资源。"""
        if self._closed:
            return
        self._closed = True

        # 取消读取协程
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # 终止子进程
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass

        # 关闭 fd
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        logger.info("terminal_session_closed", session_id=self.session_id)


class TerminalSessionManager:
    """管理所有终端会话。"""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}

    async def create_session(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        owner_agent_id: str = "",
    ) -> str:
        """创建并启动一个终端会话。返回 session_id。"""
        session_id = str(uuid4())
        session = TerminalSession(
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
        """获取一个终端会话。"""
        return self._sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        """关闭并移除一个终端会话。"""
        session = self._sessions.pop(session_id, None)
        if session:
            await session.close()

    async def close_sessions_for_agent(self, agent_id: str) -> None:
        """关闭某个 Agent 拥有的所有会话。"""
        to_close = [
            sid for sid, s in self._sessions.items()
            if s.owner_agent_id == agent_id
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
        """清理超时的 idle session。返回清理数量。"""
        to_close = [
            sid for sid, s in self._sessions.items()
            if s.idle_seconds() > max_idle_seconds or not s.is_alive()
        ]
        for sid in to_close:
            await self.close_session(sid)
        return len(to_close)

    async def close_all(self) -> None:
        """关闭所有会话。"""
        sids = list(self._sessions.keys())
        for sid in sids:
            await self.close_session(sid)

    @property
    def session_count(self) -> int:
        return len(self._sessions)
