from __future__ import annotations

import signal
from pathlib import Path

from tyrant_core.runtime.file_tools import build_file_tools
from tyrant_core.runtime.terminal_tools import build_terminal_tools
from tyrant_core.runtime.truncation import truncate_if_needed


class FakeTerminalSession:
    def __init__(self, *, read_output: str = "ready\n") -> None:
        self.read_output = read_output
        self.alive = True
        self.writes: list[str] = []
        self.signals: list[int] = []
        self.fail_write = False

    async def read(self, timeout: float = 0.0) -> str:
        return self.read_output

    def is_alive(self) -> bool:
        return self.alive

    async def write(self, data: str) -> None:
        if self.fail_write:
            raise RuntimeError("session closed")
        self.writes.append(data)

    async def send_signal(self, sig: int) -> None:
        self.signals.append(sig)


class FakeTerminalSessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, FakeTerminalSession] = {}
        self.raise_on_create: RuntimeError | None = None
        self.create_calls: list[dict] = []
        self.closed_sessions: list[str] = []
        self._counter = 0

    async def create_session(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict | None = None,
        owner_agent_id: str = "",
    ) -> str:
        if self.raise_on_create is not None:
            raise self.raise_on_create
        self._counter += 1
        session_id = f"session-{self._counter}"
        self.sessions[session_id] = FakeTerminalSession()
        self.create_calls.append({
            "command": command,
            "cwd": cwd,
            "env": env,
            "owner_agent_id": owner_agent_id,
        })
        return session_id

    def get_session(self, session_id: str) -> FakeTerminalSession | None:
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        self.closed_sessions.append(session_id)
        self.sessions.pop(session_id, None)


async def test_v2_file_tools_cover_common_and_error_paths(tmp_path) -> None:
    tools = {tool.__name__: tool for tool in build_file_tools()}
    file_read = tools["file_read"]
    file_grep = tools["file_grep"]

    sample = tmp_path / "sample.txt"
    sample.write_text("alpha\nbeta\nbeta-two\ngamma\n", encoding="utf-8")
    binary = tmp_path / "sample.bin"
    binary.write_bytes(b"\xff\xfe\x00")

    read_result = await file_read(str(sample), 2, 3)
    read_overflow = await file_read(str(sample), 99, 120)
    read_missing = await file_read(str(tmp_path / "missing.txt"))
    read_binary = await file_read(str(binary))

    grep_result = await file_grep(str(sample), "beta", max_matches=1)
    grep_all = await file_grep(str(sample), "beta", max_matches=10)
    grep_invalid = await file_grep(str(sample), "[")
    grep_missing = await file_grep(str(tmp_path / "missing.txt"), "beta")

    assert read_result == {
        "content": "2|beta\n3|beta-two",
        "total_lines": 4,
        "range": "2-3",
    }
    assert read_overflow == {
        "content": "",
        "total_lines": 4,
        "note": "起始行 99 超出文件总行数 4",
    }
    assert read_missing == {"error": f"文件不存在: {tmp_path / 'missing.txt'}"}
    assert read_binary == {"error": f"文件非文本格式: {binary}"}
    assert grep_result == {
        "matches": [{"line": 2, "content": "beta"}],
        "total_matches": 1,
        "truncated": True,
        "total_lines": 4,
    }
    assert grep_all == {
        "matches": [
            {"line": 2, "content": "beta"},
            {"line": 3, "content": "beta-two"},
        ],
        "total_matches": 2,
        "truncated": False,
        "total_lines": 4,
    }
    assert "无效的正则表达式" in grep_invalid["error"]
    assert grep_missing == {"error": f"文件不存在: {tmp_path / 'missing.txt'}"}


async def test_v2_terminal_tools_cover_start_read_write_signal_and_close() -> None:
    session_manager = FakeTerminalSessionManager()
    tools = {
        tool.__name__: tool
        for tool in build_terminal_tools("agent-1", session_manager, settings=object())
    }

    started = await tools["terminal_start"](
        command="bash",
        cwd="/tmp/work",
        env={"A": "1"},
    )
    session_id = started["session_id"]
    session = session_manager.get_session(session_id)
    assert session is not None

    session.fail_write = True
    read_result = await tools["terminal_read"](session_id, timeout=0.2)
    write_error = await tools["terminal_write"](session_id, "pwd\n")
    session.fail_write = False
    write_ok = await tools["terminal_write"](session_id, "ls\n")
    bad_signal = await tools["terminal_signal"](session_id, "SIGUSR1")
    signal_result = await tools["terminal_signal"](session_id, "SIGTERM")
    closed = await tools["terminal_close"](session_id)

    session_manager.raise_on_create = RuntimeError("spawn failed")
    start_error = await tools["terminal_start"]("python")
    missing_read = await tools["terminal_read"]("missing")
    missing_write = await tools["terminal_write"]("missing", "echo hi\n")
    missing_signal = await tools["terminal_signal"]("missing")
    missing_close = await tools["terminal_close"]("missing")

    assert started == {"session_id": session_id, "status": "started"}
    assert session_manager.create_calls == [{
        "command": "bash",
        "cwd": "/tmp/work",
        "env": {"A": "1"},
        "owner_agent_id": "agent-1",
    }]
    assert read_result == {"output": "ready\n", "alive": True}
    assert write_error == {"error": "session closed"}
    assert write_ok == {"status": "written", "bytes": 3}
    assert session.writes == ["ls\n"]
    assert bad_signal == {"error": "不支持的信号: SIGUSR1"}
    assert signal_result == {"status": "signal_sent", "signal": "SIGTERM"}
    assert session.signals == [signal.SIGTERM]
    assert closed == {"status": "closed"}
    assert session_manager.closed_sessions == [session_id]
    assert start_error == {"error": "spawn failed"}
    assert missing_read == {"error": "会话不存在: missing"}
    assert missing_write == {"error": "会话不存在: missing"}
    assert missing_signal == {"error": "会话不存在: missing"}
    assert missing_close == {"error": "会话不存在: missing"}


def test_v2_truncation_persists_large_output_and_keeps_small_output(tmp_path) -> None:
    small = truncate_if_needed(
        "short text",
        data_dir=str(tmp_path),
        max_lines=10,
        max_chars=100,
        context_label="message",
    )
    large_content = "\n".join(f"line {index} {'x' * 20}" for index in range(8))

    truncated = truncate_if_needed(
        large_content,
        data_dir=str(tmp_path),
        max_lines=4,
        max_chars=40,
        context_label="tool_result",
    )
    output_dir = tmp_path / "truncated_outputs"
    saved_files = list(output_dir.glob("tool_result_*.log"))

    assert small == "short text"
    assert len(saved_files) == 1
    assert saved_files[0].read_text(encoding="utf-8") == large_content
    assert "[内容已截断]" in truncated
    assert str(saved_files[0]) in truncated
    assert "--- 以下是尾部截取 ---" in truncated
    assert "file_read" in truncated
    assert "file_grep" in truncated


def test_v2_truncation_can_trigger_by_lines_without_tail_char_trim(tmp_path) -> None:
    content = "\n".join(f"line-{index}" for index in range(6))

    truncated = truncate_if_needed(
        content,
        data_dir=str(tmp_path),
        max_lines=4,
        max_chars=200,
        context_label="message",
    )

    assert "[内容已截断]" in truncated
    assert "--- 以下是尾部截取 ---\nline-4\nline-5" in truncated
    assert "line-4" in truncated
