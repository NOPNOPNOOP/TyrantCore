"""Terminal Tools 集成测试。"""

from __future__ import annotations

import pytest

from tyrant_core.adapters.terminal import TerminalSessionManager
from tyrant_core.config import Settings
from tyrant_core.runtime.terminal_tools import build_terminal_tools


@pytest.fixture
async def setup():
    """创建 session manager 和 tools。"""
    mgr = TerminalSessionManager()
    settings = Settings(data_dir="/tmp/tyrant_test")
    tools_list = build_terminal_tools("agent-1", mgr, settings)
    tools = {fn.__name__: fn for fn in tools_list}
    yield {"mgr": mgr, "tools": tools}
    await mgr.close_all()


class TestTerminalTools:
    """测试 terminal_* tools。"""

    async def test_start_and_read(self, setup):
        """terminal_start + terminal_read 基本流程。"""
        tools = setup["tools"]
        result = await tools["terminal_start"]("echo hello world")
        assert "session_id" in result
        assert result["status"] == "started"

        sid = result["session_id"]
        output = await tools["terminal_read"](sid, timeout=3.0)
        assert "hello world" in output["output"]

    async def test_write_and_read(self, setup):
        """terminal_write + terminal_read 交互。"""
        tools = setup["tools"]
        result = await tools["terminal_start"]("cat")
        sid = result["session_id"]

        await tools["terminal_write"](sid, "test line\n")
        output = await tools["terminal_read"](sid, timeout=3.0)
        assert "test line" in output["output"]

    async def test_signal(self, setup):
        """terminal_signal 发送信号。"""
        tools = setup["tools"]
        result = await tools["terminal_start"]("sleep 100")
        sid = result["session_id"]

        sig_result = await tools["terminal_signal"](sid, "SIGTERM")
        assert sig_result["status"] == "signal_sent"

    async def test_close(self, setup):
        """terminal_close 关闭会话。"""
        tools, mgr = setup["tools"], setup["mgr"]
        result = await tools["terminal_start"]("sleep 10")
        sid = result["session_id"]
        assert mgr.session_count == 1

        close_result = await tools["terminal_close"](sid)
        assert close_result["status"] == "closed"
        assert mgr.session_count == 0

    async def test_read_nonexistent_session(self, setup):
        """读取不存在的 session 返回错误。"""
        tools = setup["tools"]
        result = await tools["terminal_read"]("nonexistent")
        assert "error" in result

    async def test_write_nonexistent_session(self, setup):
        """写入不存在的 session 返回错误。"""
        tools = setup["tools"]
        result = await tools["terminal_write"]("nonexistent", "test")
        assert "error" in result

    async def test_signal_nonexistent_session(self, setup):
        """向不存在的 session 发信号返回错误。"""
        tools = setup["tools"]
        result = await tools["terminal_signal"]("nonexistent")
        assert "error" in result

    async def test_invalid_signal(self, setup):
        """无效的信号名返回错误。"""
        tools = setup["tools"]
        result = await tools["terminal_start"]("sleep 10")
        sid = result["session_id"]
        result = await tools["terminal_signal"](sid, "INVALID")
        assert "error" in result

    async def test_start_with_cwd(self, setup):
        """terminal_start 指定工作目录。"""
        tools = setup["tools"]
        result = await tools["terminal_start"]("pwd", cwd="/tmp")
        sid = result["session_id"]
        output = await tools["terminal_read"](sid, timeout=3.0)
        assert "tmp" in output["output"]
