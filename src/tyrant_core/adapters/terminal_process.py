"""终端进程监管。"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tyrant_core.adapters.terminal_driver import TerminalPtyDriver


class TerminalProcessSupervisor:
    """负责 pty + 子进程生命周期。"""

    def __init__(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        driver: TerminalPtyDriver | None = None,
    ) -> None:
        from tyrant_core.adapters.terminal_driver import TerminalPtyDriver

        self.command = command
        self.cwd = cwd
        self.env = env
        self.driver = driver or TerminalPtyDriver()
        self._process: asyncio.subprocess.Process | None = None
        self._master_fd: int | None = None
        self._slave_fd: int | None = None

    @property
    def pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid

    async def start(self) -> int:
        master_fd, slave_fd = self.driver.open()
        self._master_fd = master_fd
        self._slave_fd = slave_fd

        proc_env = os.environ.copy()
        if self.env:
            proc_env.update(self.env)
        proc_env.setdefault("TERM", "xterm-256color")

        try:
            self._process = await self.driver.spawn_process(
                self.command,
                slave_fd=slave_fd,
                cwd=self.cwd,
                env=proc_env,
            )
        except Exception:
            self._safe_close_fd(self._slave_fd)
            self._safe_close_fd(self._master_fd)
            self._slave_fd = None
            self._master_fd = None
            raise

        self._safe_close_fd(self._slave_fd)
        self._slave_fd = None
        return master_fd

    async def send_signal(self, sig: int) -> None:
        if self._process and self._process.returncode is None:
            self._process.send_signal(sig)

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.returncode is None

    async def shutdown(self, timeout: float = 3.0) -> None:
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass

    def _safe_close_fd(self, fd: int | None) -> None:
        if fd is None:
            return
        try:
            self.driver.close_fd(fd)
        except OSError:
            pass
