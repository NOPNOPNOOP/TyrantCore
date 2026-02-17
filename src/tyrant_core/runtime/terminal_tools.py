"""终端操作 tools。Agent 通过 tool call 主动管理终端会话。"""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tyrant_core.adapters.terminal import TerminalSessionManager
    from tyrant_core.config import Settings


def build_terminal_tools(
    agent_id: str,
    session_manager: TerminalSessionManager,
    settings: Settings,
) -> list[Callable]:
    """为指定 Agent 构建终端操作 tool 列表。"""

    async def terminal_start(
        command: str,
        cwd: str | None = None,
        env: dict | None = None,
    ) -> dict:
        """启动一个终端会话。

        Args:
            command: 要执行的命令（如 "bash", "python", "codex"）
            cwd: 工作目录（默认为当前目录）
            env: 额外环境变量（会合并到当前环境）
        """
        try:
            session_id = await session_manager.create_session(
                command=command,
                cwd=cwd,
                env=env,
                owner_agent_id=agent_id,
            )
            return {"session_id": session_id, "status": "started"}
        except Exception as e:
            return {"error": str(e)}

    async def terminal_read(
        session_id: str,
        timeout: float = 5.0,
    ) -> dict:
        """读取终端会话的输出。

        等待模式：最多等 timeout 秒，有输出就返回。

        Args:
            session_id: 会话 ID
            timeout: 等待超时秒数（默认 5 秒）
        """
        session = session_manager.get_session(session_id)
        if not session:
            return {"error": f"会话不存在: {session_id}"}

        output = await session.read(timeout=timeout)
        return {
            "output": output,
            "alive": session.is_alive(),
        }

    async def terminal_write(
        session_id: str,
        input: str,
    ) -> dict:
        """向终端会话写入内容（stdin）。

        Args:
            session_id: 会话 ID
            input: 要写入的内容（会自动发送）
        """
        session = session_manager.get_session(session_id)
        if not session:
            return {"error": f"会话不存在: {session_id}"}

        try:
            await session.write(input)
            return {"status": "written", "bytes": len(input)}
        except RuntimeError as e:
            return {"error": str(e)}

    async def terminal_signal(
        session_id: str,
        signal_name: str = "SIGINT",
    ) -> dict:
        """向终端会话的进程发送信号。

        Args:
            session_id: 会话 ID
            signal_name: 信号名称（如 "SIGINT", "SIGTERM", "SIGKILL"）
        """
        session = session_manager.get_session(session_id)
        if not session:
            return {"error": f"会话不存在: {session_id}"}

        sig_map = {
            "SIGINT": signal.SIGINT,
            "SIGTERM": signal.SIGTERM,
            "SIGKILL": signal.SIGKILL,
            "SIGSTOP": signal.SIGSTOP,
            "SIGCONT": signal.SIGCONT,
        }
        sig = sig_map.get(signal_name.upper())
        if sig is None:
            return {"error": f"不支持的信号: {signal_name}"}

        await session.send_signal(sig)
        return {"status": "signal_sent", "signal": signal_name}

    async def terminal_close(session_id: str) -> dict:
        """关闭终端会话。

        Args:
            session_id: 会话 ID
        """
        session = session_manager.get_session(session_id)
        if not session:
            return {"error": f"会话不存在: {session_id}"}

        await session_manager.close_session(session_id)
        return {"status": "closed"}

    return [
        terminal_start,
        terminal_read,
        terminal_write,
        terminal_signal,
        terminal_close,
    ]
