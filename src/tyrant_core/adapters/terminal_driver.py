"""pty 层驱动。隔离底层 fd 读写与子进程启动。"""

from __future__ import annotations

import asyncio
import errno
import os


class TerminalPtyDriver:
    """封装 pty fd 的打开、读写与子进程启动。"""

    def open(self) -> tuple[int, int]:
        return os.openpty()

    async def spawn_process(
        self,
        command: str,
        *,
        slave_fd: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
        )

    async def read(self, fd: int, size: int = 4096) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.blocking_read, fd, size)

    def blocking_read(self, fd: int, size: int = 4096) -> str:
        """阻塞读取 fd。预期性的 pty/EOF 错误统一转为空字符串。"""
        try:
            raw = os.read(fd, size)
            if not raw:
                return ""
            return raw.decode("utf-8", errors="replace")
        except OSError as exc:
            if exc.errno in (errno.EIO, errno.EBADF, errno.EAGAIN):
                return ""
            raise

    async def write(self, fd: int, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.write, fd, data)

    def close_fd(self, fd: int) -> None:
        os.close(fd)
