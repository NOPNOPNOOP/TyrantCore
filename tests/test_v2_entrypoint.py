from __future__ import annotations

import runpy
import sys
from types import SimpleNamespace

import tyrant_core.__main__ as entrypoint


class FakeStack:
    def __init__(self, gateway) -> None:
        self.gateway = gateway


class FakeGateway:
    def __init__(self) -> None:
        self.api = FakeApi()
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class FakeApi:
    pass


class FakeCli:
    def __init__(self, gateway: FakeGateway) -> None:
        self.gateway = gateway
        self.ran = False

    async def run(self) -> None:
        self.ran = True


class FakeTtyStream:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


async def test_v2_main_prefers_cli_in_tty_mode(monkeypatch) -> None:
    gateway = FakeGateway()
    created: dict[str, FakeCli] = {}

    monkeypatch.setattr(
        entrypoint,
        "build_embedded_runtime_gateway",
        lambda: FakeStack(gateway),
    )

    def fake_cli_factory(value: FakeGateway) -> FakeCli:
        cli = FakeCli(value)
        created["cli"] = cli
        return cli

    monkeypatch.setattr(entrypoint, "CliAdapter", fake_cli_factory)
    monkeypatch.setattr(
        entrypoint,
        "sys",
        SimpleNamespace(
            stdin=FakeTtyStream(True),
            stdout=FakeTtyStream(True),
        ),
    )

    await entrypoint.main()

    assert created["cli"].gateway is gateway
    assert created["cli"].ran is True
    assert gateway.started is False
    assert gateway.stopped is False


async def test_v2_main_runs_gateway_in_non_tty_mode(monkeypatch) -> None:
    gateway = FakeGateway()
    printed: list[str] = []

    monkeypatch.setattr(
        entrypoint,
        "build_embedded_runtime_gateway",
        lambda: FakeStack(gateway),
    )
    monkeypatch.setattr(
        entrypoint,
        "sys",
        SimpleNamespace(
            stdin=FakeTtyStream(False),
            stdout=FakeTtyStream(False),
        ),
    )
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **kwargs: printed.append(" ".join(str(item) for item in args)),
    )

    await entrypoint.main()

    assert gateway.started is True
    assert gateway.stopped is True
    assert printed == [
        "TyrantCore v2 is active.",
        "gateway=FakeGateway api=FakeApi",
    ]


def test_v2_module_entrypoint_invokes_asyncio_run(monkeypatch) -> None:
    called: list[object] = []

    def fake_run(coro) -> None:
        called.append(coro)
        coro.close()

    monkeypatch.setattr("asyncio.run", fake_run)
    monkeypatch.delitem(sys.modules, "tyrant_core.__main__", raising=False)

    runpy.run_module("tyrant_core.__main__", run_name="__main__")

    assert len(called) == 1
