from __future__ import annotations

import asyncio
import errno
import os

import pytest

from tyrant_core.adapters.terminal_driver import TerminalPtyDriver
from tyrant_core.adapters.terminal_process import TerminalProcessSupervisor
from tyrant_core.adapters.terminal_session import (
    TerminalOutputBuffer,
    TerminalSession,
    TerminalSessionManager,
)


class FakeProcess:
    def __init__(
        self,
        *,
        pid: int = 321,
        wait_delay: float = 0.0,
        raise_lookup_on_terminate: bool = False,
    ) -> None:
        self.pid = pid
        self.wait_delay = wait_delay
        self.raise_lookup_on_terminate = raise_lookup_on_terminate
        self.returncode: int | None = None
        self.signals: list[int] = []
        self.terminated = False
        self.killed = False

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def terminate(self) -> None:
        if self.raise_lookup_on_terminate:
            raise ProcessLookupError
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        if self.wait_delay and not self.killed:
            await asyncio.sleep(self.wait_delay)
        if self.killed:
            self.returncode = -9
        elif self.terminated:
            self.returncode = -15
        else:
            self.returncode = 0
        return self.returncode


class FakeDriver:
    def __init__(
        self,
        *,
        process: FakeProcess | None = None,
        reads: list[str] | None = None,
    ) -> None:
        self.process = process or FakeProcess()
        self.reads = list(reads or [])
        self.opened = (11, 12)
        self.spawn_calls: list[dict] = []
        self.closed_fds: list[int] = []
        self.writes: list[tuple[int, bytes]] = []

    def open(self) -> tuple[int, int]:
        return self.opened

    async def spawn_process(
        self,
        command: str,
        *,
        slave_fd: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> FakeProcess:
        self.spawn_calls.append({
            "command": command,
            "slave_fd": slave_fd,
            "cwd": cwd,
            "env": env,
        })
        return self.process

    async def read(self, fd: int, size: int = 4096) -> str:
        if self.reads:
            return self.reads.pop(0)
        return ""

    async def write(self, fd: int, data: bytes) -> None:
        self.writes.append((fd, data))

    def close_fd(self, fd: int) -> None:
        self.closed_fds.append(fd)


class FailingSpawnDriver(FakeDriver):
    async def spawn_process(
        self,
        command: str,
        *,
        slave_fd: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> FakeProcess:
        raise RuntimeError("spawn failed")

    def close_fd(self, fd: int) -> None:
        self.closed_fds.append(fd)
        raise OSError("close failed")


class ExceptionalReadDriver(FakeDriver):
    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self.exc = exc

    async def read(self, fd: int, size: int = 4096) -> str:
        raise self.exc


class CloseErrorDriver(FakeDriver):
    def close_fd(self, fd: int) -> None:
        self.closed_fds.append(fd)
        raise OSError("close failed")


class FakeManagedSession:
    def __init__(
        self,
        *,
        session_id: str,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        owner_agent_id: str = "",
        alive: bool = True,
        idle: float = 0.0,
    ) -> None:
        self.session_id = session_id
        self.command = command
        self.cwd = cwd
        self.env = env
        self.owner_agent_id = owner_agent_id
        self.alive = alive
        self.idle = idle
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return self.alive

    def idle_seconds(self) -> float:
        return self.idle


async def test_v2_terminal_output_buffer_waits_and_clears() -> None:
    buffer = TerminalOutputBuffer()

    async def append_later() -> None:
        await asyncio.sleep(0.01)
        buffer.append("hello")
        buffer.append(" world")

    task = asyncio.create_task(append_later())
    result = await buffer.read(timeout=0.2)
    await task

    assert result == "hello world"
    assert buffer.pop() == ""
    assert await buffer.read(timeout=0.01) == ""
    buffer.append("")
    assert await buffer.read(timeout=0) == ""


async def test_v2_terminal_pty_driver_wraps_os_operations(monkeypatch) -> None:
    driver = TerminalPtyDriver()
    subprocess_call: dict[str, object] = {}
    writes: list[tuple[int, bytes]] = []
    closes: list[int] = []

    monkeypatch.setattr(os, "openpty", lambda: (21, 22))

    async def fake_create_subprocess_exec(*args, **kwargs):
        subprocess_call["args"] = args
        subprocess_call["kwargs"] = kwargs
        return "proc"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(driver, "blocking_read", lambda fd, size=4096: "chunk")
    monkeypatch.setattr(os, "write", lambda fd, data: writes.append((fd, data)) or len(data))
    monkeypatch.setattr(os, "close", lambda fd: closes.append(fd))

    assert driver.open() == (21, 22)
    proc = await driver.spawn_process(
        "echo hi",
        slave_fd=22,
        cwd="/tmp/work",
        env={"HELLO": "WORLD"},
    )
    assert proc == "proc"
    assert subprocess_call["args"] == ("/bin/sh", "-c", "echo hi")
    assert subprocess_call["kwargs"] == {
        "stdin": 22,
        "stdout": 22,
        "stderr": 22,
        "cwd": "/tmp/work",
        "env": {"HELLO": "WORLD"},
    }
    assert await driver.read(21, 8) == "chunk"
    await driver.write(21, b"pwd\n")
    driver.close_fd(21)
    assert writes == [(21, b"pwd\n")]
    assert closes == [21]


def test_v2_terminal_pty_driver_blocking_read_handles_expected_errno(
    monkeypatch,
) -> None:
    driver = TerminalPtyDriver()

    monkeypatch.setattr(os, "read", lambda fd, size: b"hello")
    assert driver.blocking_read(1) == "hello"

    monkeypatch.setattr(os, "read", lambda fd, size: b"")
    assert driver.blocking_read(1) == ""

    def raise_expected(fd, size):
        raise OSError(errno.EIO, "expected")

    monkeypatch.setattr(os, "read", raise_expected)
    assert driver.blocking_read(1) == ""

    def raise_unexpected(fd, size):
        raise OSError(errno.EPERM, "unexpected")

    monkeypatch.setattr(os, "read", raise_unexpected)
    with pytest.raises(OSError):
        driver.blocking_read(1)


async def test_v2_terminal_process_supervisor_starts_and_shutdowns() -> None:
    driver = FakeDriver()
    supervisor = TerminalProcessSupervisor(
        "echo hello",
        cwd="/tmp/work",
        env={"HELLO": "WORLD"},
        driver=driver,
    )

    master_fd = await supervisor.start()

    assert master_fd == 11
    assert supervisor.pid == 321
    assert supervisor.is_alive() is True
    assert driver.spawn_calls[0]["command"] == "echo hello"
    assert driver.spawn_calls[0]["slave_fd"] == 12
    assert driver.spawn_calls[0]["cwd"] == "/tmp/work"
    assert driver.spawn_calls[0]["env"]["HELLO"] == "WORLD"
    assert "TERM" in driver.spawn_calls[0]["env"]
    assert driver.closed_fds == [12]

    await supervisor.send_signal(15)
    await supervisor.shutdown()

    assert driver.process.signals == [15]
    assert driver.process.terminated is True
    assert supervisor.is_alive() is False


async def test_v2_terminal_process_supervisor_covers_timeout_and_lookup_error() -> None:
    timeout_driver = FakeDriver(process=FakeProcess(wait_delay=0.05))
    timeout_supervisor = TerminalProcessSupervisor("sleep 1", driver=timeout_driver)
    await timeout_supervisor.start()
    await timeout_supervisor.shutdown(timeout=0.01)
    assert timeout_driver.process.killed is True

    lookup_driver = FakeDriver(process=FakeProcess(raise_lookup_on_terminate=True))
    lookup_supervisor = TerminalProcessSupervisor("sleep 1", driver=lookup_driver)
    await lookup_supervisor.start()
    await lookup_supervisor.shutdown()
    assert lookup_driver.process.terminated is False


async def test_v2_terminal_process_supervisor_handles_failed_start_and_noop_edges() -> None:
    driver = FailingSpawnDriver()
    supervisor = TerminalProcessSupervisor("boom", driver=driver)

    assert supervisor.pid is None
    assert supervisor.is_alive() is False

    await supervisor.send_signal(9)
    await supervisor.shutdown()

    with pytest.raises(RuntimeError, match="spawn failed"):
        await supervisor.start()

    assert driver.closed_fds == [12, 11]
    assert supervisor._master_fd is None
    assert supervisor._slave_fd is None


def test_v2_terminal_process_supervisor_safe_close_ignores_none_fd() -> None:
    driver = FakeDriver()
    supervisor = TerminalProcessSupervisor("sleep 1", driver=driver)

    supervisor._safe_close_fd(None)

    assert driver.closed_fds == []


async def test_v2_terminal_session_composes_supervisor_and_driver() -> None:
    driver = FakeDriver(reads=["boot ok\n", ""])
    supervisor = TerminalProcessSupervisor("fake-shell", driver=driver)
    session = TerminalSession(
        session_id="session-1",
        command="fake-shell",
        owner_agent_id="actor-1",
        supervisor=supervisor,
    )

    await session.start()
    await asyncio.sleep(0)
    output = await session.read(timeout=0.1)
    idle_after_read = session.idle_seconds()
    await session.write("pwd\n")
    await session.send_signal(2)
    await session.close()
    await session.close()

    assert output == "boot ok\n"
    assert idle_after_read >= 0
    assert driver.writes == [(11, b"pwd\n")]
    assert driver.process.signals == [2]
    assert 11 in driver.closed_fds
    assert session.is_alive() is False


async def test_v2_terminal_session_covers_guard_and_exception_edges() -> None:
    idle_supervisor = TerminalProcessSupervisor("fake-shell", driver=FakeDriver())
    idle_session = TerminalSession(
        session_id="idle",
        command="fake-shell",
        supervisor=idle_supervisor,
    )
    await idle_session._read_loop()

    os_error_driver = ExceptionalReadDriver(OSError(errno.EIO, "pty closed"))
    os_error_session = TerminalSession(
        session_id="os-error",
        command="fake-shell",
        supervisor=TerminalProcessSupervisor("fake-shell", driver=os_error_driver),
    )
    os_error_session._master_fd = 11
    await os_error_session._read_loop()

    cancelled_driver = ExceptionalReadDriver(asyncio.CancelledError())
    cancelled_session = TerminalSession(
        session_id="cancelled",
        command="fake-shell",
        supervisor=TerminalProcessSupervisor("fake-shell", driver=cancelled_driver),
    )
    cancelled_session._master_fd = 11
    await cancelled_session._read_loop()

    close_error_driver = CloseErrorDriver()
    close_error_session = TerminalSession(
        session_id="close-error",
        command="fake-shell",
        supervisor=TerminalProcessSupervisor("fake-shell", driver=close_error_driver),
    )
    with pytest.raises(RuntimeError, match="Session is closed"):
        await close_error_session.write("pwd\n")

    close_error_session._master_fd = 11
    close_error_session._reader_task = asyncio.create_task(asyncio.sleep(10))
    await close_error_session.close()

    assert close_error_session._master_fd is None
    assert close_error_driver.closed_fds == [11]


async def test_v2_terminal_session_covers_noop_close_and_manager_empty_agent() -> None:
    session = TerminalSession(
        session_id="noop",
        command="fake-shell",
        supervisor=TerminalProcessSupervisor("fake-shell", driver=FakeDriver()),
    )
    session._closed = True
    session._master_fd = 11

    await session._read_loop()

    session2 = TerminalSession(
        session_id="noop-close",
        command="fake-shell",
        supervisor=TerminalProcessSupervisor("fake-shell", driver=FakeDriver()),
    )
    await session2.close()

    manager = TerminalSessionManager(session_factory=lambda **kwargs: FakeManagedSession(**kwargs))
    await manager.close_sessions_for_agent("missing")
    assert manager.session_count == 0


async def test_v2_terminal_session_manager_supports_factory_and_cleanup() -> None:
    created: list[FakeManagedSession] = []

    def factory(**kwargs) -> FakeManagedSession:
        session = FakeManagedSession(**kwargs)
        created.append(session)
        return session

    manager = TerminalSessionManager(session_factory=factory)
    sid1 = await manager.create_session("sleep 1", owner_agent_id="a1")
    sid2 = await manager.create_session("sleep 1", owner_agent_id="a1")
    sid3 = await manager.create_session("sleep 1", owner_agent_id="a2")

    assert all(session.started for session in created)
    assert manager.get_session(sid1) is created[0]

    created[1].idle = 99
    created[2].alive = False

    cleaned = await manager.cleanup_idle(max_idle_seconds=10)
    assert cleaned == 2
    assert manager.session_count == 1

    await manager.close_sessions_for_agent("a1")
    assert manager.session_count == 0
    assert created[0].closed is True
    assert created[1].closed is True
    assert created[2].closed is True

    sid4 = await manager.create_session("sleep 1", owner_agent_id="a3")
    assert manager.get_session(sid4) is created[3]
    assert manager.session_count == 1

    await manager.close_session("missing")
    await manager.close_all()
    assert manager.session_count == 0
    assert created[3].closed is True


async def test_v2_terminal_session_real_pty_smoke() -> None:
    session = TerminalSession(
        session_id="real-pty",
        command="/bin/cat",
    )

    await session.start()
    try:
        await session.write("ping from smoke\n")
        output = await session.read(timeout=1.0)
        assert "ping from smoke" in output
        assert session.is_alive() is True
    finally:
        await session.close()
