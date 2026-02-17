"""TerminalSession + TerminalSessionManager 测试。"""

from __future__ import annotations

import asyncio

import pytest

from tyrant_core.adapters.terminal import TerminalSession, TerminalSessionManager


class TestTerminalSession:
    """测试 TerminalSession。"""

    async def test_start_and_read(self):
        """启动进程并读取输出。"""
        session = TerminalSession(
            session_id="test-1",
            command="echo hello",
        )
        await session.start()
        # 等一会让输出到达
        output = await session.read(timeout=3.0)
        await session.close()
        assert "hello" in output

    async def test_write_and_read(self):
        """写入 stdin 并读取 stdout。"""
        session = TerminalSession(
            session_id="test-2",
            command="cat",
        )
        await session.start()
        await session.write("test input\n")
        output = await session.read(timeout=3.0)
        await session.close()
        assert "test input" in output

    async def test_is_alive(self):
        """检查进程存活状态。"""
        session = TerminalSession(
            session_id="test-3",
            command="sleep 10",
        )
        await session.start()
        assert session.is_alive() is True
        await session.close()
        # close 后进程应被 kill
        assert session.is_alive() is False

    async def test_close_idempotent(self):
        """close 可以安全地调用多次。"""
        session = TerminalSession(
            session_id="test-4",
            command="echo hi",
        )
        await session.start()
        await session.close()
        await session.close()  # 不应报错

    async def test_read_timeout_returns_empty(self):
        """超时时 read 返回空字符串。"""
        session = TerminalSession(
            session_id="test-5",
            command="sleep 10",
        )
        await session.start()
        # sleep 不产生输出，应超时返回空
        output = await session.read(timeout=0.3)
        await session.close()
        assert output == ""

    async def test_custom_cwd(self):
        """自定义工作目录。"""
        session = TerminalSession(
            session_id="test-6",
            command="pwd",
            cwd="/tmp",
        )
        await session.start()
        output = await session.read(timeout=3.0)
        await session.close()
        # macOS 下 /tmp 可能是 /private/tmp 的链接
        assert "tmp" in output

    async def test_send_signal(self):
        """发送信号。"""
        import signal

        session = TerminalSession(
            session_id="test-7",
            command="sleep 100",
        )
        await session.start()
        assert session.is_alive() is True
        await session.send_signal(signal.SIGTERM)
        # 等待进程退出
        await asyncio.sleep(0.5)
        assert session.is_alive() is False
        await session.close()

    async def test_idle_seconds(self):
        """idle_seconds 反映活动时间。"""
        session = TerminalSession(
            session_id="test-8",
            command="sleep 10",
        )
        await session.start()
        await asyncio.sleep(0.2)
        assert session.idle_seconds() >= 0.1
        await session.read(timeout=0.1)  # read 更新活动时间
        assert session.idle_seconds() < 0.2
        await session.close()


class TestTerminalSessionManager:
    """测试 TerminalSessionManager。"""

    async def test_create_and_get(self):
        """创建会话后可以获取。"""
        mgr = TerminalSessionManager()
        sid = await mgr.create_session("echo hello", owner_agent_id="agent-1")
        session = mgr.get_session(sid)
        assert session is not None
        assert session.owner_agent_id == "agent-1"
        assert mgr.session_count == 1
        await mgr.close_all()

    async def test_close_session(self):
        """关闭会话后不可获取。"""
        mgr = TerminalSessionManager()
        sid = await mgr.create_session("echo hello")
        await mgr.close_session(sid)
        assert mgr.get_session(sid) is None
        assert mgr.session_count == 0

    async def test_close_sessions_for_agent(self):
        """按 agent_id 批量关闭。"""
        mgr = TerminalSessionManager()
        await mgr.create_session("sleep 10", owner_agent_id="a1")
        await mgr.create_session("sleep 10", owner_agent_id="a1")
        await mgr.create_session("sleep 10", owner_agent_id="a2")
        assert mgr.session_count == 3

        await mgr.close_sessions_for_agent("a1")
        assert mgr.session_count == 1
        await mgr.close_all()

    async def test_cleanup_idle(self):
        """清理 idle 超时的会话。"""
        mgr = TerminalSessionManager()
        await mgr.create_session("sleep 10", owner_agent_id="a1")
        await asyncio.sleep(0.3)
        cleaned = await mgr.cleanup_idle(max_idle_seconds=0.1)
        assert cleaned == 1
        assert mgr.session_count == 0

    async def test_get_nonexistent(self):
        """获取不存在的会话返回 None。"""
        mgr = TerminalSessionManager()
        assert mgr.get_session("nonexistent") is None
