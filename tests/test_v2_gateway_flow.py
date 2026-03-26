from __future__ import annotations

from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors.cli import CliConnector
from tyrant_core.runtime import ActorRuntime
from tyrant_core.store import SqliteStore


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def test_v2_gateway_cli_runtime_roundtrip(tmp_path) -> None:
    gateway = GatewayServer(store=SqliteStore(database_url=_db_url(tmp_path, "flow.db")))
    cli = CliConnector(gateway)
    runtime = ActorRuntime(gateway)
    await gateway.start()
    try:
        tyrant = await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        worker = await gateway.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
            "caller_actor_id": "tyrant",
        })

        assert tyrant["actor"]["id"] == "tyrant"
        assert worker["actor"]["id"] == "worker"

        first = await cli.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker",
            text="hello",
            external_key="cli:demo",
        )
        assert first["envelope"]["payload"]["text"] == "hello"

        pending_for_worker = await gateway.list_pending_deliveries("worker")
        assert len(pending_for_worker) == 1

        seen_by_worker: list[str] = []

        async def worker_handler(envelope: dict, delivery: dict) -> dict:
            seen_by_worker.append(envelope["payload"]["text"])
            return {
                "recipient_actor_ids": ["tyrant"],
                "payload": {"text": "pong"},
            }

        processed = await runtime.consume_once(
            actor_id="worker",
            consumer_id="worker-runtime",
            handler=worker_handler,
        )
        assert processed == 1
        assert seen_by_worker == ["hello"]

        worker_acked = await gateway.query_deliveries(actor_id="worker", state="ACKED")
        assert len(worker_acked) == 1

        pending_for_tyrant = await gateway.list_pending_deliveries("tyrant")
        assert len(pending_for_tyrant) == 1
        assert pending_for_tyrant[0]["state"] == "PENDING"

        seen_by_tyrant: list[tuple[str, str]] = []

        async def tyrant_handler(envelope: dict, delivery: dict) -> None:
            seen_by_tyrant.append((envelope["thread_id"], envelope["payload"]["text"]))
            return None

        processed_reply = await runtime.consume_once(
            actor_id="tyrant",
            consumer_id="tyrant-runtime",
            handler=tyrant_handler,
        )
        assert processed_reply == 1
        assert seen_by_tyrant == [(first["thread_id"], "pong")]

        second = await cli.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker",
            text="follow-up",
            external_key="cli:demo",
        )
        assert second["thread_id"] == first["thread_id"]
    finally:
        await gateway.stop()
