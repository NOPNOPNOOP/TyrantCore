from __future__ import annotations

from rich.console import Console

from tyrant_core.adapters.cli import CliAdapter
from tyrant_core.adapters.cli_commands import CliCommandHandler
from tyrant_core.adapters.cli_render import CliRenderer


class FakeCliAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def show_help(self) -> None:
        self.calls.append(("show_help", None))

    async def show_actors(self) -> None:
        self.calls.append(("show_actors", None))

    async def create_actor(self, *, kind: str, actor_id: str, name: str):
        self.calls.append(("create_actor", {"kind": kind, "actor_id": actor_id, "name": name}))
        return {"id": actor_id, "kind": kind, "name": name}

    def show_created_actor(self, actor: dict) -> None:
        self.calls.append(("show_created_actor", actor))

    def show_info(self, message: str, *, style: str | None = None) -> None:
        self.calls.append(("show_info", {"message": message, "style": style}))

    def set_target(self, actor_id: str) -> None:
        self.calls.append(("set_target", actor_id))

    async def send_text(self, text: str):
        self.calls.append(("send_text", text))
        return {"thread_id": "thread-1", "envelope": {"id": "env-1"}}

    def show_send_result(self, result: dict) -> None:
        self.calls.append(("show_send_result", result))

    async def show_inbox(self) -> None:
        self.calls.append(("show_inbox", None))

    async def show_sessions(self) -> None:
        self.calls.append(("show_sessions", None))

    async def show_bindings(self) -> None:
        self.calls.append(("show_bindings", None))

    async def show_deliveries(self, actor_id: str | None) -> None:
        self.calls.append(("show_deliveries", actor_id))


class FakePromptSession:
    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.prompts: list[str] = []

    async def prompt_async(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.lines.pop(0)


class FakeGateway:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1

    async def list_actors(self) -> list[dict]:
        return [{
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
        }]


async def test_v2_cli_command_handler_covers_usage_and_unknown_paths() -> None:
    adapter = FakeCliAdapter()
    handler = CliCommandHandler(adapter)

    assert await handler.handle("/actors") is True
    assert await handler.handle("/create") is True
    assert await handler.handle("/target") is True
    assert await handler.handle("/send") is True
    assert await handler.handle("/deliveries") is True
    assert await handler.handle("/wat") is True

    assert adapter.calls == [
        ("show_actors", None),
        ("show_info", {"message": "用法: /create <kind> <actor_id> <name>", "style": "yellow"}),
        ("show_info", {"message": "用法: /target <actor_id>", "style": "yellow"}),
        ("show_info", {"message": "用法: /send <text>", "style": "yellow"}),
        ("show_deliveries", None),
        ("show_info", {"message": "未知命令: /wat", "style": "yellow"}),
    ]


def test_v2_cli_renderer_renders_banner_tables_and_empty_inbox() -> None:
    console = Console(record=True, width=120)
    renderer = CliRenderer(console)

    renderer.render_banner()
    renderer.render_help()
    renderer.render_actors([{
        "id": "worker",
        "kind": "AGENT",
        "name": "Worker",
        "slug": "worker",
    }])
    renderer.render_inbox([])
    renderer.render_inbox([{
        "envelope": {
            "sender_actor_id": "worker",
            "payload": {"text": "[done]"},
        }
    }])
    renderer.render_sessions([{
        "id": "session-1",
        "actor_id": "worker",
        "external_key": "cli:test",
        "thread_id": None,
        "state": "OPEN",
    }])
    renderer.render_bindings([{
        "actor_id": "worker",
        "thread_policy": "REUSE",
    }])
    renderer.render_deliveries([{
        "id": "delivery-1",
        "recipient_actor_id": "worker",
        "envelope_id": "env-1",
        "state": "PENDING",
        "attempt_count": 2,
    }])

    output = console.export_text()
    assert "Gateway Control Plane" in output
    assert "Commands" in output
    assert "Actors" in output
    assert "inbox is empty" in output
    assert "[worker] [done]" in output
    assert "Sessions" in output
    assert "Bindings" in output
    assert "Deliveries" in output
    assert "cli:test" in output
    assert "REUSE" in output


async def test_v2_cli_adapter_run_covers_blank_command_value_error_and_quit(
    monkeypatch,
) -> None:
    gateway = FakeGateway()
    console = Console(record=True, width=120)
    prompt_session = FakePromptSession(["   ", "/help", "/actors", "hello", "/quit"])
    cli = CliAdapter(gateway, console=console, prompt_session=prompt_session)

    async def fake_bootstrap() -> None:
        return None

    async def fake_send_text(text: str, *, recipient_actor_id=None, thread_id=None):
        raise ValueError("尚未设置 target_actor_id")

    cli.bootstrap = fake_bootstrap  # type: ignore[method-assign]
    cli.send_text = fake_send_text  # type: ignore[method-assign]

    await cli.run()

    output = console.export_text()
    assert gateway.started == 1
    assert gateway.stopped == 1
    assert prompt_session.prompts == ["TyrantCore> "] * 5
    assert "Commands" in output
    assert "Actors" in output
    assert "尚未设置 target_actor_id" in output


async def test_v2_cli_adapter_send_text_requires_target() -> None:
    cli = CliAdapter(FakeGateway(), console=Console(record=True, width=120))

    try:
        await cli.send_text("hello")
    except ValueError as exc:
        assert str(exc) == "尚未设置 target_actor_id"
    else:
        raise AssertionError("expected ValueError")


async def test_v2_cli_adapter_run_covers_successful_send_path(monkeypatch) -> None:
    gateway = FakeGateway()
    console = Console(record=True, width=120)
    prompt_session = FakePromptSession(["hello", "/quit"])
    cli = CliAdapter(gateway, console=console, prompt_session=prompt_session)

    async def fake_bootstrap() -> None:
        return None

    async def fake_send_text(text: str, *, recipient_actor_id=None, thread_id=None):
        return {"thread_id": "thread-1", "envelope": {"id": "env-1"}}

    cli.bootstrap = fake_bootstrap  # type: ignore[method-assign]
    cli.target_actor_id = "worker"
    cli.send_text = fake_send_text  # type: ignore[method-assign]

    await cli.run()

    output = console.export_text()
    assert gateway.started == 1
    assert gateway.stopped == 1
    assert "sent thread=thread-1 envelope=env-1" in output
