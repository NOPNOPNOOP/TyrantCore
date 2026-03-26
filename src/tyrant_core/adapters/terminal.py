"""终端子系统兼容入口。"""

from __future__ import annotations

from tyrant_core.adapters.terminal_driver import TerminalPtyDriver
from tyrant_core.adapters.terminal_process import TerminalProcessSupervisor
from tyrant_core.adapters.terminal_session import (
    TerminalOutputBuffer,
    TerminalSession,
    TerminalSessionManager,
)

__all__ = [
    "TerminalOutputBuffer",
    "TerminalProcessSupervisor",
    "TerminalPtyDriver",
    "TerminalSession",
    "TerminalSessionManager",
]
