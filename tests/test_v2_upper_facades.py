from __future__ import annotations

from datetime import datetime, timezone

from tyrant_core.api.system_api import SystemAPI
from tyrant_core.gateway.control_plane import GatewayControlPlane
from tyrant_core.gateway.delivery_control import GatewayDeliveryControl
from tyrant_core.gateway.delivery_engine import DeliveryLease
from tyrant_core.models import Delivery, DeliveryState


def _delivery(delivery_id: str = "delivery-1") -> Delivery:
    return Delivery(
        id=delivery_id,
        envelope_id="env-1",
        recipient_actor_id="actor-1",
        state=DeliveryState.PENDING,
        created_at=datetime.now(timezone.utc),
    )


class FakeCommandResult:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class AsyncFacade:
    def __init__(self, returns: dict[str, object] | None = None) -> None:
        self.returns = dict(returns or {})
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        async def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.returns.get(name)

        return method


async def test_v2_system_api_proxies_commands_queries_and_policy() -> None:
    commands = AsyncFacade({
        "create_actor": FakeCommandResult({"actor": {"id": "actor-1"}}),
        "create_org_edge": FakeCommandResult({"org_edge": {"id": "edge-1"}}),
        "issue_grant": FakeCommandResult({"grant": {"id": "grant-1"}}),
        "open_thread": FakeCommandResult({"thread": {"id": "thread-1"}}),
        "send_envelope": FakeCommandResult({"envelope": {"id": "env-1"}}),
        "create_work_item": FakeCommandResult({"work_item": {"id": "work-1"}}),
        "register_attachment": FakeCommandResult({"attachment": {"id": "attachment-1"}}),
        "report_presence": FakeCommandResult({"presence": {"attachment_id": "attachment-1"}}),
    })
    queries = AsyncFacade({
        "get_actor": {"id": "actor-1"},
        "list_actors": [{"id": "actor-1"}],
        "list_org_edges": [{"id": "edge-1"}],
        "list_grants": [{"id": "grant-1"}],
        "get_thread": {"id": "thread-1"},
        "get_envelope": {"id": "env-1"},
        "query_envelopes": [{"id": "env-1"}],
        "get_delivery": {"id": "delivery-1"},
        "list_pending_deliveries": [{"id": "delivery-1"}],
        "query_deliveries": [{"id": "delivery-1"}],
        "get_work_item": {"id": "work-1"},
        "query_work_items": [{"id": "work-1"}],
        "query_sessions": [{"id": "session-1"}],
        "query_attachments": [{"id": "attachment-1"}],
        "get_presence": {"attachment_id": "attachment-1"},
        "get_domain_events": [{"id": "event-1"}],
    })
    policies = AsyncFacade({"can": {"allowed": True}})
    management = AsyncFacade({
        "create_profile": {"id": "profile-1"},
        "get_profile": {"id": "profile-1"},
        "query_profiles": [{"id": "profile-1"}],
        "create_control": {"id": "control-1"},
        "get_control": {"actor_id": "actor-1"},
        "query_controls": [{"actor_id": "actor-1"}],
        "assign_manager": {"actor_id": "actor-1", "manager_actor_id": "manager-1"},
        "set_profile": {"actor_id": "actor-1", "profile_id": "profile-1"},
        "set_desired_state": {"actor_id": "actor-1", "desired_state": "PAUSED"},
    })
    provisioning = AsyncFacade({
        "submit_request": {"id": "request-1"},
        "get_request": {"id": "request-1"},
        "claim_request": True,
        "query_requests": [{"id": "request-1"}],
        "execute_request": {"id": "record-1"},
        "get_record": {"id": "record-1"},
        "query_records": [{"id": "record-1"}],
    })
    runtime_supervisor = AsyncFacade({
        "spawn_actor_runtime": {"attachment": {"id": "attachment-1"}},
        "get_runtime_status": {"actor_id": "actor-1", "state": "ONLINE"},
        "stop_actor_runtime": None,
    })
    reconciler = AsyncFacade({
        "reconcile_actor": True,
        "reconcile_all": 2,
    })
    provisioning_work_items = AsyncFacade({
        "process": {
            "work_item": {"id": "work-1", "status": "COMPLETED"},
            "provision_record": {"id": "record-1"},
            "reconciled": True,
        },
    })
    provisioning_queue = AsyncFacade({
        "process_next": {
            "work_item": {"id": "work-2", "status": "COMPLETED"},
            "provision_record": {"id": "record-2"},
            "reconciled": False,
        },
    })
    api = SystemAPI(
        store=object(),
        event_bus=object(),
        policies=policies,
        commands=commands,
        queries=queries,
        management=management,
        provisioning=provisioning,
        provisioning_work_items=provisioning_work_items,
        provisioning_queue=provisioning_queue,
        runtime_supervisor=runtime_supervisor,
        reconciler=reconciler,
    )
    since = datetime.now(timezone.utc)

    assert await api.create_actor({"id": "actor-1"}) == {"actor": {"id": "actor-1"}}
    assert await api.create_org_edge({"id": "edge-1"}) == {"org_edge": {"id": "edge-1"}}
    assert await api.issue_grant({"id": "grant-1"}) == {"grant": {"id": "grant-1"}}
    assert await api.open_thread({"id": "thread-1"}) == {"thread": {"id": "thread-1"}}
    assert await api.send_envelope({"id": "env-1"}) == {"envelope": {"id": "env-1"}}
    assert await api.create_work_item({"id": "work-1"}) == {"work_item": {"id": "work-1"}}
    assert await api.register_attachment({"id": "attachment-1"}) == {"attachment": {"id": "attachment-1"}}
    assert await api.report_presence({"attachment_id": "attachment-1"}) == {"presence": {"attachment_id": "attachment-1"}}
    assert await api.create_actor_profile({"id": "profile-1"}) == {"id": "profile-1"}
    assert await api.get_actor_profile("profile-1") == {"id": "profile-1"}
    assert await api.query_actor_profiles(archived=False, limit=8) == [{"id": "profile-1"}]
    assert await api.create_actor_control({"id": "control-1"}) == {"id": "control-1"}
    assert await api.get_actor_control("actor-1") == {"actor_id": "actor-1"}
    assert await api.query_actor_controls(manager_actor_id="manager-1", desired_state="ACTIVE", runtime_kind="LOCAL", limit=9) == [{"actor_id": "actor-1"}]
    assert await api.assign_actor_manager("actor-1", "manager-1") == {"actor_id": "actor-1", "manager_actor_id": "manager-1"}
    assert await api.set_actor_profile("actor-1", "profile-1") == {"actor_id": "actor-1", "profile_id": "profile-1"}
    assert await api.set_actor_desired_state("actor-1", "PAUSED") == {"actor_id": "actor-1", "desired_state": "PAUSED"}
    assert await api.submit_provision_request({"id": "request-1"}) == {"id": "request-1"}
    assert await api.get_provision_request("request-1") == {"id": "request-1"}
    assert await api.claim_provision_request("request-1", "executor-1") is True
    assert await api.query_provision_requests(status="PENDING", limit=10) == [{"id": "request-1"}]
    assert await api.execute_provision_request("request-1", executor_actor_id="executor-1") == {"id": "record-1"}
    assert await api.get_provision_record("record-1") == {"id": "record-1"}
    assert await api.query_provision_records(request_id="request-1", limit=11) == [{"id": "record-1"}]
    requested = await api.request_actor_provision({
        "requested_by_actor_id": "actor-1",
        "blueprint": {"name": "worker", "parent_actor_id": "actor-1"},
    })
    assert requested["work_item"]["id"] == "work-1"
    assert requested["provision_request"]["id"] == "request-1"
    assert await api.process_provision_work_item(
        "work-1",
        executor_actor_id="executor-1",
        auto_reconcile=False,
    ) == {
        "work_item": {"id": "work-1", "status": "COMPLETED"},
        "provision_record": {"id": "record-1"},
        "reconciled": True,
    }
    assert await api.process_next_provision_work_item(
        owner_actor_id="factory-1",
        executor_actor_id="executor-1",
        auto_reconcile=False,
        scan_limit=6,
    ) == {
        "work_item": {"id": "work-2", "status": "COMPLETED"},
        "provision_record": {"id": "record-2"},
        "reconciled": False,
    }
    assert await api.spawn_actor_runtime("actor-1") == {"attachment": {"id": "attachment-1"}}
    assert await api.get_runtime_status("actor-1") == {"actor_id": "actor-1", "state": "ONLINE"}
    assert await api.stop_actor_runtime("actor-1") is None
    assert await api.reconcile_actor("actor-1") is True
    assert await api.reconcile_all(limit=12) == 2
    assert await api.get_actor("actor-1") == {"id": "actor-1"}
    assert await api.list_actors(kind="AGENT") == [{"id": "actor-1"}]
    assert await api.list_org_edges(kind="REPORTS_TO") == [{"id": "edge-1"}]
    assert await api.list_grants("actor-1") == [{"id": "grant-1"}]
    assert await api.get_thread("thread-1") == {"id": "thread-1"}
    assert await api.get_envelope("env-1") == {"id": "env-1"}
    assert await api.query_envelopes(thread_id="thread-1", sender_actor_id="actor-1", limit=2) == [{"id": "env-1"}]
    assert await api.get_delivery("delivery-1") == {"id": "delivery-1"}
    assert await api.list_pending_deliveries("actor-1") == [{"id": "delivery-1"}]
    assert await api.query_deliveries(actor_id="actor-1", state="PENDING", limit=3) == [{"id": "delivery-1"}]
    assert await api.get_work_item("work-1") == {"id": "work-1"}
    assert await api.query_work_items(owner_actor_id="actor-1", status="OPEN", limit=4) == [{"id": "work-1"}]
    assert await api.query_sessions(actor_id="actor-1", surface="cli", external_key="cli:test", state="OPEN", limit=5) == [{"id": "session-1"}]
    assert await api.query_attachments(actor_id="actor-1", kind="TERMINAL", limit=6) == [{"id": "attachment-1"}]
    assert await api.get_presence("attachment-1") == {"attachment_id": "attachment-1"}
    assert await api.get_domain_events(since=since, limit=7) == [{"id": "event-1"}]
    assert await api.can("envelope.send", "actor-1", target_id="actor-2") == {"allowed": True}


async def test_v2_gateway_delivery_control_and_control_plane_proxy_remaining_paths() -> None:
    api = AsyncFacade({
        "list_pending_deliveries": [{"id": "delivery-1"}],
        "query_deliveries": [{"id": "delivery-2"}],
    })
    deliveries = AsyncFacade({
        "lease": [DeliveryLease(delivery=_delivery(), consumer_id="consumer-1")],
        "ack": None,
        "fail": None,
    })
    delivery_control = GatewayDeliveryControl(api=api, deliveries=deliveries)

    leased = await delivery_control.lease_deliveries(
        actor_id="actor-1",
        consumer_id="consumer-1",
        limit=2,
        lease_seconds=20,
    )
    await delivery_control.ack_delivery("delivery-1")
    await delivery_control.fail_delivery("delivery-1", error={"msg": "boom"})
    pending = await delivery_control.list_pending_deliveries("actor-1")
    queried = await delivery_control.query_deliveries(actor_id="actor-1", state="PENDING", limit=3)

    assert leased[0]["consumer_id"] == "consumer-1"
    assert leased[0]["delivery"]["id"] == "delivery-1"
    assert pending == [{"id": "delivery-1"}]
    assert queried == [{"id": "delivery-2"}]

    control = GatewayControlPlane(
        api=object(),
        bindings=object(),
        sessions=object(),
        deliveries=object(),
    )
    control.session_control = AsyncFacade({
        "open_session": {"id": "session-1"},
        "bind_session_thread": {"id": "session-1", "thread_id": "thread-1"},
        "get_session": {"id": "session-1"},
        "query_sessions": [{"id": "session-1"}],
    })
    control.binding_control = AsyncFacade({
        "register_binding": {"id": "binding-1"},
        "query_bindings": [{"id": "binding-1"}],
        "resolve_binding": {"id": "binding-1"},
    })
    control.delivery_control = AsyncFacade({
        "lease_deliveries": [{"delivery": {"id": "delivery-1"}}],
        "ack_delivery": None,
        "fail_delivery": None,
        "list_pending_deliveries": [{"id": "delivery-1"}],
        "query_deliveries": [{"id": "delivery-1"}],
    })
    control.presence_control = AsyncFacade({
        "register_attachment": {"attachment": {"id": "attachment-1"}},
        "report_presence": {"presence": {"attachment_id": "attachment-1"}},
        "query_attachments": [{"id": "attachment-1"}],
        "get_presence": {"attachment_id": "attachment-1"},
    })

    assert await control.get_session("session-1") == {"id": "session-1"}
    assert await control.resolve_binding(surface="cli", external_key="cli:test", account_id="acc-1") == {"id": "binding-1"}
    await control.fail_delivery("delivery-1", error={"msg": "boom"})
    assert await control.list_pending_deliveries("actor-1") == [{"id": "delivery-1"}]
