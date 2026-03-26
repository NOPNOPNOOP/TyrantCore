from __future__ import annotations

from rich.console import Console

from tyrant_core.adapters.cli import CliAdapter
from tyrant_core.adapters.cli_bootstrap import CliBootstrapper
from tyrant_core.adapters.cli_commands import CliCommandHandler
from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors.cli import CliConnector
from tyrant_core.runtime import ActorRuntime
from tyrant_core.store import SqliteStore


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def test_v2_cli_bootstrapper_is_idempotent(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "cli-bootstrap.db")))
    connector = CliConnector(gateway)
    bootstrapper = CliBootstrapper(
        gateway,
        connector=connector,
        tyrant_actor_id="tyrant",
        external_key="cli:test",
    )

    await gateway.start()
    try:
        await bootstrapper.ensure_ready()
        await bootstrapper.ensure_ready()

        actors = await gateway.list_actors(id="tyrant")
        bindings = await gateway.query_bindings(
            surface="cli",
            actor_id="tyrant",
            limit=100,
        )

        assert len(actors) == 1
        assert len(bindings) == 1
        assert bindings[0]["external_key"] == "cli:test"
    finally:
        await gateway.stop()


async def test_v2_cli_command_handler_drives_split_components(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "cli-components.db")))
    runtime = ActorRuntime(gateway)
    console = Console(record=True, width=120)
    cli = CliAdapter(gateway, external_key="cli:test", console=console)
    handler = CliCommandHandler(cli)

    await gateway.start()
    try:
        await cli.bootstrap()

        assert await handler.handle("/help") is True
        assert await handler.handle("/create AGENT worker Worker") is True
        assert await handler.handle("/target worker") is True
        assert await handler.handle("/send hello gateway") is True

        async def worker_handler(envelope: dict, delivery: dict) -> dict:
            return {
                "recipient_actor_ids": ["tyrant"],
                "payload": {"text": envelope["payload"]["text"].upper()},
            }

        processed = await runtime.consume_once(
            actor_id="worker",
            consumer_id="worker-runtime",
            handler=worker_handler,
        )
        assert processed == 1

        assert await handler.handle("/inbox") is True
        assert await handler.handle("/sessions") is True
        assert await handler.handle("/bindings") is True
        assert await handler.handle("/deliveries worker") is True
        assert await handler.handle("/quit") is False

        output = console.export_text()
        assert "created actor=worker kind=AGENT" in output
        assert "target=worker" in output
        assert "sent thread=" in output
        assert "[worker] HELLO GATEWAY" in output
        assert "Commands" in output
        assert "Sessions" in output
        assert "Bindings" in output
        assert "Deliveries" in output
    finally:
        await gateway.stop()
