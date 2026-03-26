from __future__ import annotations

import asyncio

from rich.console import Console

from tyrant_core.adapters.cli import CliAdapter
from tyrant_core.gateway.connectors import CliConnector, WebSocketConnector, WebhookConnector
from tyrant_core.runtime import ActorRuntime, build_embedded_runtime_gateway


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


class FakePromptSession:
    def __init__(self, steps: list[tuple[float, str]]) -> None:
        self.steps = list(steps)
        self.prompts: list[str] = []

    async def prompt_async(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.steps:
            raise EOFError("no scripted CLI input left")
        delay, line = self.steps.pop(0)
        await asyncio.sleep(delay)
        return line


async def test_v2_embedded_cli_run_smoke_recovers_worker_and_persists_cli_state(
    tmp_path,
) -> None:
    database_url = _db_url(tmp_path, "embedded-cli-run-smoke.db")

    seed = build_embedded_runtime_gateway(
        database_url=database_url,
        enable_terminal=False,
        enable_remote=False,
        enable_external=False,
        local_poll_interval=0.01,
    ).gateway
    await seed.start()
    try:
        await seed.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await seed.create_actor({
            "id": "worker-smoke",
            "kind": "AGENT",
            "name": "Worker Smoke",
            "slug": "worker_smoke",
            "metadata": {
                "local_runtime": {
                    "behavior": "template_reply",
                    "text": "smoke reply",
                }
            },
            "caller_actor_id": "tyrant",
        })
        await seed.create_actor_control({
            "id": "control-worker-smoke",
            "actor_id": "worker-smoke",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })
    finally:
        await seed.stop()

    prompt_session = FakePromptSession([
        (0.0, "/target worker-smoke"),
        (0.0, "/send hello smoke"),
        (0.1, "/inbox"),
        (0.0, "/sessions"),
        (0.0, "/bindings"),
        (0.0, "/quit"),
    ])
    console = Console(record=True, width=120)
    stack = build_embedded_runtime_gateway(
        database_url=database_url,
        enable_terminal=False,
        enable_remote=False,
        enable_external=False,
        local_poll_interval=0.01,
    )
    cli = CliAdapter(
        stack.gateway,
        external_key="cli:smoke",
        console=console,
        prompt_session=prompt_session,
    )

    await cli.run()

    output = console.export_text()
    assert "target=worker-smoke" in output
    assert "sent thread=" in output
    assert "[worker-smoke] smoke reply" in output
    assert "Sessions" in output
    assert "Bindings" in output
    assert prompt_session.prompts == ["TyrantCore> "] * 6

    reopened = build_embedded_runtime_gateway(
        database_url=database_url,
        enable_terminal=False,
        enable_remote=False,
        enable_external=False,
        local_poll_interval=0.01,
    ).gateway
    await reopened.start()
    try:
        for _ in range(50):
            status = await reopened.get_runtime_status("worker-smoke")
            if status is not None:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("worker smoke runtime was not recovered on reopen")

        sessions = await reopened.query_sessions(
            surface="cli",
            external_key="cli:smoke",
        )
        bindings = await reopened.query_bindings(
            surface="cli",
            actor_id="tyrant",
            limit=100,
        )
        acked = await reopened.query_deliveries(
            actor_id="tyrant",
            state="ACKED",
            limit=100,
        )

        assert status["driver"] == "local-runtime"
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] is not None
        assert any(item["external_key"] == "cli:smoke" for item in bindings)
        assert len(acked) == 1
    finally:
        await reopened.stop()


async def test_v2_embedded_provisioning_worker_smoke_creates_and_recovers_local_runtime(
    tmp_path,
) -> None:
    stack = build_embedded_runtime_gateway(
        database_url=_db_url(tmp_path, "embedded-provisioning-smoke.db"),
        enable_terminal=False,
        enable_remote=False,
        enable_external=False,
        local_poll_interval=0.01,
    )
    gateway = stack.gateway
    worker = gateway.register_provisioning_worker(
        owner_actor_id="tyrant",
        executor_actor_id="factory",
        poll_interval=0.01,
        auto_reconcile=True,
    )
    cli = CliConnector(gateway)
    runtime = ActorRuntime(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "manager",
            "kind": "AGENT",
            "name": "Manager",
            "slug": "manager",
            "caller_actor_id": "tyrant",
        })

        requested = await gateway.request_actor_provision({
            "requested_by_actor_id": "tyrant",
            "work_item_owner_actor_id": "tyrant",
            "caller_actor_id": "tyrant",
            "blueprint": {
                "name": "Provisioned Worker",
                "parent_actor_id": "manager",
                "manager_actor_id": "tyrant",
                "bootstrap_spec": {
                    "local_runtime": {
                        "behavior": "template_reply",
                        "text": "provisioned reply",
                    }
                },
                "runtime_spec": {
                    "runtime_kind": "LOCAL",
                },
                "metadata": {
                    "actor_id": "worker-provisioned",
                    "slug": "worker_provisioned",
                },
            },
        })

        for _ in range(50):
            work_item = await gateway.get_work_item(requested["work_item"]["id"])
            status = await gateway.get_runtime_status("worker-provisioned")
            if work_item["status"] == "COMPLETED" and status is not None:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("provisioned worker was not created and started in time")

        await cli.send_text(
            sender_actor_id="tyrant",
            recipient_actor_id="worker-provisioned",
            text="hello provisioned smoke",
            external_key="cli:provision-smoke",
        )

        for _ in range(50):
            pending = await gateway.list_pending_deliveries("tyrant")
            if pending:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("provisioned worker did not reply in time")

        seen: list[str] = []

        async def tyrant_handler(envelope: dict, delivery: dict) -> None:
            del delivery
            seen.append(envelope["payload"]["text"])
            return None

        processed = await runtime.consume_once(
            actor_id="tyrant",
            consumer_id="tyrant-smoke-runtime",
            handler=tyrant_handler,
        )

        assert work_item["status"] == "COMPLETED"
        assert status is not None
        assert status["driver"] == "local-runtime"
        assert processed == 1
        assert seen == ["provisioned reply"]
    finally:
        await gateway.stop()

    assert worker.running is False


async def test_v2_webhook_connector_smoke_routes_external_input_through_binding_and_runtime(
    tmp_path,
) -> None:
    stack = build_embedded_runtime_gateway(
        database_url=_db_url(tmp_path, "embedded-webhook-smoke.db"),
        enable_terminal=False,
        enable_remote=False,
        enable_external=False,
        local_poll_interval=0.01,
    )
    gateway = stack.gateway
    webhook = WebhookConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "hook-user",
            "kind": "EXTERNAL",
            "name": "Hook User",
            "slug": "hook_user",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor({
            "id": "worker-webhook",
            "kind": "AGENT",
            "name": "Worker Webhook",
            "slug": "worker_webhook",
            "metadata": {
                "local_runtime": {
                    "behavior": "template_reply",
                    "text": "webhook reply",
                }
            },
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-worker-webhook",
            "actor_id": "worker-webhook",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })
        changed = await gateway.reconcile_actor("worker-webhook")
        await gateway.register_binding({
            "id": "binding-hook-user",
            "surface": "webhook",
            "actor_id": "hook-user",
            "external_key": "hook:user-1",
            "thread_policy": "REUSE",
        })

        result = await webhook.receive_event(
            external_key="hook:user-1",
            recipient_actor_id="worker-webhook",
            payload={"text": "hello webhook smoke"},
        )

        for _ in range(50):
            pending = await gateway.list_pending_deliveries("hook-user")
            if pending:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("webhook sender did not receive reply in time")

        envelope = await gateway.get_envelope(pending[0]["envelope_id"])
        sessions = await gateway.query_sessions(
            surface="webhook",
            external_key="hook:user-1",
        )

        assert result["envelope"]["kind"] == "WEBHOOK_EVENT"
        assert changed is True
        assert envelope["payload"]["text"] == "webhook reply"
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] == result["thread_id"]
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_smoke_tracks_connection_presence_and_reply_flow(
    tmp_path,
) -> None:
    stack = build_embedded_runtime_gateway(
        database_url=_db_url(tmp_path, "embedded-websocket-smoke.db"),
        enable_terminal=False,
        enable_remote=False,
        enable_external=False,
        local_poll_interval=0.01,
    )
    gateway = stack.gateway
    websocket = WebSocketConnector(gateway)

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user",
            "kind": "EXTERNAL",
            "name": "Ws User",
            "slug": "ws_user",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor({
            "id": "worker-ws",
            "kind": "AGENT",
            "name": "Worker Ws",
            "slug": "worker_ws",
            "metadata": {
                "local_runtime": {
                    "behavior": "template_reply",
                    "text": "websocket reply",
                }
            },
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-worker-ws",
            "actor_id": "worker-ws",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })
        changed = await gateway.reconcile_actor("worker-ws")
        await gateway.register_binding({
            "id": "binding-ws-user",
            "surface": "websocket",
            "actor_id": "ws-user",
            "external_key": "ws:user-1",
            "thread_policy": "REUSE",
        })

        await websocket.start()
        await websocket.connect(
            external_key="ws:user-1",
            connection_id="conn-smoke",
            metadata={"client": "browser"},
        )
        result = await websocket.send_text(
            external_key="ws:user-1",
            recipient_actor_id="worker-ws",
            text="hello websocket smoke",
        )

        for _ in range(50):
            messages = await websocket.receive_once(connection_id="conn-smoke")
            if messages:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("websocket sender did not receive reply in time")

        attachment_presence = await gateway.get_presence("websocket:conn-smoke")
        envelope = messages[0]["envelope"]

        assert changed is True
        assert result["thread_id"] is not None
        assert attachment_presence is not None
        assert attachment_presence["status"] == "ONLINE"
        assert envelope["payload"]["text"] == "websocket reply"

        await websocket.disconnect("conn-smoke")
        detached_presence = await gateway.get_presence("websocket:conn-smoke")

        assert detached_presence is not None
        assert detached_presence["status"] == "OFFLINE"
    finally:
        await gateway.stop()


async def test_v2_websocket_connector_smoke_broadcasts_reply_to_multiple_connections(
    tmp_path,
) -> None:
    stack = build_embedded_runtime_gateway(
        database_url=_db_url(tmp_path, "embedded-websocket-broadcast-smoke.db"),
        enable_terminal=False,
        enable_remote=False,
        enable_external=False,
        local_poll_interval=0.01,
    )
    gateway = stack.gateway
    websocket = WebSocketConnector(gateway, routing_mode="BROADCAST")

    await gateway.start()
    try:
        await gateway.create_actor({
            "id": "tyrant",
            "kind": "TYRANT",
            "name": "Tyrant",
            "slug": "tyrant",
            "bypass_policy": True,
        })
        await gateway.create_actor({
            "id": "ws-user-broadcast",
            "kind": "EXTERNAL",
            "name": "Ws User Broadcast",
            "slug": "ws_user_broadcast",
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor({
            "id": "worker-ws-broadcast",
            "kind": "AGENT",
            "name": "Worker Ws Broadcast",
            "slug": "worker_ws_broadcast",
            "metadata": {
                "local_runtime": {
                    "behavior": "template_reply",
                    "text": "broadcast websocket reply",
                }
            },
            "caller_actor_id": "tyrant",
        })
        await gateway.create_actor_control({
            "id": "control-worker-ws-broadcast",
            "actor_id": "worker-ws-broadcast",
            "manager_actor_id": "tyrant",
            "desired_state": "ACTIVE",
            "runtime_kind": "LOCAL",
        })
        changed = await gateway.reconcile_actor("worker-ws-broadcast")
        await gateway.register_binding({
            "id": "binding-ws-user-broadcast",
            "surface": "websocket",
            "actor_id": "ws-user-broadcast",
            "external_key": "ws:user-broadcast",
            "thread_policy": "REUSE",
        })

        await websocket.start()
        await websocket.connect(
            external_key="ws:user-broadcast",
            connection_id="conn-broadcast-1",
        )
        await websocket.connect(
            external_key="ws:user-broadcast",
            connection_id="conn-broadcast-2",
        )
        result = await websocket.send_text(
            external_key="ws:user-broadcast",
            recipient_actor_id="worker-ws-broadcast",
            text="hello websocket broadcast smoke",
        )

        for _ in range(50):
            messages_first = await websocket.receive_once(
                connection_id="conn-broadcast-1"
            )
            if messages_first:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("websocket first connection did not receive reply in time")

        messages_second = await websocket.receive_once(connection_id="conn-broadcast-2")

        assert changed is True
        assert result["thread_id"] is not None
        assert [item["envelope"]["payload"]["text"] for item in messages_first] == [
            "broadcast websocket reply"
        ]
        assert [item["envelope"]["payload"]["text"] for item in messages_second] == [
            "broadcast websocket reply"
        ]
    finally:
        await gateway.stop()
