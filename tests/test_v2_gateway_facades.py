from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tyrant_core.gateway.application import GatewayApplication
from tyrant_core.gateway.binding_control import GatewayBindingControl
from tyrant_core.gateway.binding_resolver import BindingMatch
from tyrant_core.gateway.manager import GatewayManager
from tyrant_core.gateway.server import GatewayServer
from tyrant_core.gateway.session_control import GatewaySessionControl
from tyrant_core.models import Session, SessionMode, SessionState


def _session(session_id: str = "session-1") -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        id=session_id,
        surface="cli",
        actor_id="actor-1",
        external_key="cli:test",
        thread_id="thread-1",
        mode=SessionMode.PERSISTENT,
        state=SessionState.OPEN,
        metadata={"source": "test"},
        created_at=now,
        last_seen_at=now,
    )


class FakeDumpModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        assert mode == "json"
        return dict(self.payload)


class AsyncFacade:
    def __init__(self, returns: dict[str, object] | None = None) -> None:
        self.returns = dict(returns or {})
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        async def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.returns.get(name)
            if isinstance(value, Exception):
                raise value
            return value

        return method


class FakeSessions:
    def __init__(self) -> None:
        self.open_result = _session()
        self.bound_result = _session("session-2")
        self.get_result: Session | None = _session()
        self.query_result = [_session(), _session("session-2")]
        self.calls: list[tuple[str, tuple, dict]] = []

    async def open_session(self, **kwargs):
        self.calls.append(("open_session", (), dict(kwargs)))
        return self.open_result

    async def bind_thread(self, session_id: str, thread_id: str):
        self.calls.append(("bind_thread", (session_id, thread_id), {}))
        return self.bound_result

    async def get(self, session_id: str):
        self.calls.append(("get", (session_id,), {}))
        return self.get_result

    async def query(self, **kwargs):
        self.calls.append(("query", (), dict(kwargs)))
        return list(self.query_result)


class FakeBindings:
    def __init__(self) -> None:
        self.register_result = FakeDumpModel({"id": "binding-1"})
        self.query_result = [
            BindingMatch(
                binding_id="binding-1",
                actor_id="actor-1",
                surface="cli",
                external_key="cli:test",
                account_id="acc-1",
                thread_policy="REUSE",
                fixed_thread_id="thread-1",
                priority=3,
                metadata={"x": 1},
            )
        ]
        self.resolve_result: BindingMatch | None = self.query_result[0]
        self.calls: list[tuple[str, tuple, dict]] = []

    async def register(self, match: BindingMatch):
        self.calls.append(("register", (match,), {}))
        return self.register_result

    async def query(self, **kwargs):
        self.calls.append(("query", (), dict(kwargs)))
        return list(self.query_result)

    async def resolve(self, surface: str, external_key: str, *, account_id: str | None = None):
        self.calls.append((
            "resolve",
            (surface, external_key),
            {"account_id": account_id},
        ))
        return self.resolve_result


class FakeConnector:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class FakeStore:
    def __init__(self) -> None:
        self.initialized = 0
        self.closed = 0

    async def initialize(self) -> None:
        self.initialized += 1

    async def close(self) -> None:
        self.closed += 1


class FakeManager:
    def __init__(self, *, stop_error: Exception | None = None) -> None:
        self.started = 0
        self.stopped = 0
        self.stop_error = stop_error
        self.registered: list[str] = []

    def register(self, service) -> None:
        self.registered.append(service.name)

    async def start_all(self) -> None:
        self.started += 1

    async def stop_all(self) -> None:
        self.stopped += 1
        if self.stop_error is not None:
            raise self.stop_error


async def test_v2_gateway_application_proxies_to_system_api() -> None:
    api = AsyncFacade({
        "create_actor": {"actor": {"id": "actor-1"}},
        "get_actor": {"id": "actor-1"},
        "list_actors": [{"id": "actor-1"}],
        "create_org_edge": {"org_edge": {"id": "edge-1"}},
        "list_org_edges": [{"id": "edge-1"}],
        "issue_grant": {"grant": {"id": "grant-1"}},
        "list_grants": [{"id": "grant-1"}],
        "open_thread": {"thread": {"id": "thread-1"}},
        "get_thread": {"id": "thread-1"},
        "send_envelope": {"envelope": {"id": "env-1"}},
        "get_envelope": {"id": "env-1"},
        "query_envelopes": [{"id": "env-1"}],
        "create_work_item": {"work_item": {"id": "work-1"}},
        "create_actor_profile": {"id": "profile-1"},
        "get_actor_profile": {"id": "profile-1"},
        "query_actor_profiles": [{"id": "profile-1"}],
        "create_actor_control": {"id": "control-1"},
        "get_actor_control": {"actor_id": "actor-1"},
        "query_actor_controls": [{"actor_id": "actor-1"}],
        "assign_actor_manager": {"actor_id": "actor-1", "manager_actor_id": "manager-1"},
        "set_actor_profile": {"actor_id": "actor-1", "profile_id": "profile-1"},
        "set_actor_desired_state": {"actor_id": "actor-1", "desired_state": "PAUSED"},
        "submit_provision_request": {"id": "request-1"},
        "get_provision_request": {"id": "request-1"},
        "claim_provision_request": True,
        "query_provision_requests": [{"id": "request-1"}],
        "execute_provision_request": {"id": "record-1"},
        "get_provision_record": {"id": "record-1"},
        "query_provision_records": [{"id": "record-1"}],
        "request_actor_provision": {
            "work_item": {"id": "work-2"},
            "provision_request": {"id": "request-2"},
        },
        "process_provision_work_item": {
            "work_item": {"id": "work-2", "status": "COMPLETED"},
            "provision_record": {"id": "record-2"},
            "reconciled": True,
        },
        "process_next_provision_work_item": {
            "work_item": {"id": "work-3", "status": "COMPLETED"},
            "provision_record": {"id": "record-3"},
            "reconciled": False,
        },
        "spawn_actor_runtime": {"attachment": {"id": "attachment-1"}},
        "stop_actor_runtime": None,
        "get_runtime_status": {"actor_id": "actor-1", "state": "ONLINE"},
        "reconcile_actor": True,
        "reconcile_all": 2,
        "get_work_item": {"id": "work-1"},
        "query_work_items": [{"id": "work-1"}],
        "get_domain_events": [{"id": "event-1"}],
        "can": {"allowed": True},
    })
    app = GatewayApplication(api)
    since = datetime.now(timezone.utc)

    assert await app.create_actor({"id": "actor-1"}) == {"actor": {"id": "actor-1"}}
    assert await app.get_actor("actor-1") == {"id": "actor-1"}
    assert await app.list_actors(kind="AGENT") == [{"id": "actor-1"}]
    assert await app.create_org_edge({"id": "edge-1"}) == {"org_edge": {"id": "edge-1"}}
    assert await app.list_org_edges(kind="REPORTS_TO") == [{"id": "edge-1"}]
    assert await app.issue_grant({"id": "grant-1"}) == {"grant": {"id": "grant-1"}}
    assert await app.list_grants("actor-1") == [{"id": "grant-1"}]
    assert await app.open_thread({"id": "thread-1"}) == {"thread": {"id": "thread-1"}}
    assert await app.get_thread("thread-1") == {"id": "thread-1"}
    assert await app.ingest({"payload": {"text": "hi"}}) == {"envelope": {"id": "env-1"}}
    assert await app.get_envelope("env-1") == {"id": "env-1"}
    assert await app.query_envelopes(thread_id="thread-1", sender_actor_id="actor-1", limit=3) == [{"id": "env-1"}]
    assert await app.create_work_item({"id": "work-1"}) == {"work_item": {"id": "work-1"}}
    assert await app.create_actor_profile({"id": "profile-1"}) == {"id": "profile-1"}
    assert await app.get_actor_profile("profile-1") == {"id": "profile-1"}
    assert await app.query_actor_profiles(archived=False, limit=5) == [{"id": "profile-1"}]
    assert await app.create_actor_control({"id": "control-1"}) == {"id": "control-1"}
    assert await app.get_actor_control("actor-1") == {"actor_id": "actor-1"}
    assert await app.query_actor_controls(manager_actor_id="manager-1", desired_state="ACTIVE", runtime_kind="LOCAL", limit=6) == [{"actor_id": "actor-1"}]
    assert await app.assign_actor_manager("actor-1", "manager-1") == {"actor_id": "actor-1", "manager_actor_id": "manager-1"}
    assert await app.set_actor_profile("actor-1", "profile-1") == {"actor_id": "actor-1", "profile_id": "profile-1"}
    assert await app.set_actor_desired_state("actor-1", "PAUSED") == {"actor_id": "actor-1", "desired_state": "PAUSED"}
    assert await app.submit_provision_request({"id": "request-1"}) == {"id": "request-1"}
    assert await app.get_provision_request("request-1") == {"id": "request-1"}
    assert await app.claim_provision_request("request-1", "executor-1") is True
    assert await app.query_provision_requests(status="PENDING", limit=7) == [{"id": "request-1"}]
    assert await app.execute_provision_request("request-1", executor_actor_id="executor-1") == {"id": "record-1"}
    assert await app.get_provision_record("record-1") == {"id": "record-1"}
    assert await app.query_provision_records(request_id="request-1", limit=8) == [{"id": "record-1"}]
    assert await app.request_actor_provision({"requested_by_actor_id": "actor-1"}) == {
        "work_item": {"id": "work-2"},
        "provision_request": {"id": "request-2"},
    }
    assert await app.process_provision_work_item(
        "work-2",
        executor_actor_id="executor-1",
        auto_reconcile=False,
    ) == {
        "work_item": {"id": "work-2", "status": "COMPLETED"},
        "provision_record": {"id": "record-2"},
        "reconciled": True,
    }
    assert await app.process_next_provision_work_item(
        owner_actor_id="factory-1",
        executor_actor_id="executor-1",
        auto_reconcile=False,
        scan_limit=6,
    ) == {
        "work_item": {"id": "work-3", "status": "COMPLETED"},
        "provision_record": {"id": "record-3"},
        "reconciled": False,
    }
    assert await app.spawn_actor_runtime("actor-1") == {"attachment": {"id": "attachment-1"}}
    assert await app.stop_actor_runtime("actor-1") is None
    assert await app.get_runtime_status("actor-1") == {"actor_id": "actor-1", "state": "ONLINE"}
    assert await app.reconcile_actor("actor-1") is True
    assert await app.reconcile_all(limit=9) == 2
    assert await app.get_work_item("work-1") == {"id": "work-1"}
    assert await app.query_work_items(owner_actor_id="actor-1", status="OPEN", limit=4) == [{"id": "work-1"}]
    assert await app.get_domain_events(since=since, limit=5) == [{"id": "event-1"}]
    assert await app.can("envelope.send", "actor-1", target_id="actor-2") == {"allowed": True}

    assert ("send_envelope", ({"payload": {"text": "hi"}},), {}) in api.calls
    assert ("get_domain_events", (), {"since": since, "limit": 5}) in api.calls
    assert ("can", ("envelope.send", "actor-1"), {"target_id": "actor-2"}) in api.calls


async def test_v2_gateway_session_control_converts_models_to_json() -> None:
    sessions = FakeSessions()
    control = GatewaySessionControl(sessions)

    opened = await control.open_session(
        surface="cli",
        actor_id="actor-1",
        external_key="cli:test",
        thread_id="thread-1",
        mode="PERSISTENT",
        binding_id="binding-1",
        metadata={"x": 1},
    )
    bound = await control.bind_session_thread("session-1", "thread-2")
    fetched = await control.get_session("session-1")
    queried = await control.query_sessions(
        actor_id="actor-1",
        surface="cli",
        external_key="cli:test",
        state="OPEN",
        limit=2,
    )

    sessions.get_result = None
    missing = await control.get_session("missing")

    assert opened.id == "session-1"
    assert bound is sessions.bound_result
    assert fetched is not None
    assert fetched["id"] == "session-1"
    assert [item["id"] for item in queried] == ["session-1", "session-2"]
    assert missing is None


async def test_v2_gateway_binding_control_handles_register_query_and_resolve() -> None:
    bindings = FakeBindings()
    control = GatewayBindingControl(bindings)

    registered = await control.register_binding({
        "id": "binding-1",
        "actor_id": "actor-1",
        "surface": "cli",
        "peer": "cli:test",
        "account_id": "acc-1",
        "fixed_thread_id": "thread-1",
        "priority": 3,
        "metadata": {"x": 1},
    })
    queried = await control.query_bindings(surface="cli", actor_id="actor-1", limit=2)
    resolved = await control.resolve_binding(surface="cli", external_key="cli:test", account_id="acc-1")
    bindings.resolve_result = None
    missing = await control.resolve_binding(surface="cli", external_key="missing")

    match = bindings.calls[0][1][0]
    assert match.binding_id == "binding-1"
    assert match.external_key == "cli:test"
    assert match.thread_policy == "REUSE"
    assert registered == {"id": "binding-1"}
    assert queried == [{
        "binding_id": "binding-1",
        "actor_id": "actor-1",
        "surface": "cli",
        "external_key": "cli:test",
        "account_id": "acc-1",
        "thread_policy": "REUSE",
        "fixed_thread_id": "thread-1",
        "priority": 3,
        "metadata": {"x": 1},
    }]
    assert resolved == queried[0]
    assert missing is None


async def test_v2_gateway_manager_registers_sorts_and_drives_connectors() -> None:
    manager = GatewayManager()
    zeta = FakeConnector("zeta")
    alpha = FakeConnector("alpha")
    alpha_new = FakeConnector("alpha")

    manager.register(zeta)
    manager.register(alpha)
    manager.register(alpha_new)

    assert manager.list_connectors() == ["alpha", "zeta"]
    assert manager.list_services() == ["alpha", "zeta"]

    await manager.start_all()
    await manager.stop_all()

    assert alpha.started == 0
    assert alpha_new.started == 1
    assert zeta.started == 1
    assert alpha_new.stopped == 1
    assert zeta.stopped == 1


async def test_v2_gateway_server_start_stop_and_proxy_methods() -> None:
    store = FakeStore()
    manager = FakeManager()
    application = AsyncFacade({
        "create_actor": {"actor": {"id": "actor-1"}},
        "get_actor": {"id": "actor-1"},
        "list_actors": [{"id": "actor-1"}],
        "create_org_edge": {"org_edge": {"id": "edge-1"}},
        "list_org_edges": [{"id": "edge-1"}],
        "issue_grant": {"grant": {"id": "grant-1"}},
        "list_grants": [{"id": "grant-1"}],
        "open_thread": {"thread": {"id": "thread-1"}},
        "get_thread": {"id": "thread-1"},
        "ingest": {"envelope": {"id": "env-1"}},
        "get_envelope": {"id": "env-1"},
        "query_envelopes": [{"id": "env-1"}],
        "create_work_item": {"work_item": {"id": "work-1"}},
        "create_actor_profile": {"id": "profile-1"},
        "get_actor_profile": {"id": "profile-1"},
        "query_actor_profiles": [{"id": "profile-1"}],
        "create_actor_control": {"id": "control-1"},
        "get_actor_control": {"actor_id": "actor-1"},
        "query_actor_controls": [{"actor_id": "actor-1"}],
        "assign_actor_manager": {"actor_id": "actor-1", "manager_actor_id": "manager-1"},
        "set_actor_profile": {"actor_id": "actor-1", "profile_id": "profile-1"},
        "set_actor_desired_state": {"actor_id": "actor-1", "desired_state": "PAUSED"},
        "submit_provision_request": {"id": "request-1"},
        "get_provision_request": {"id": "request-1"},
        "claim_provision_request": True,
        "query_provision_requests": [{"id": "request-1"}],
        "execute_provision_request": {"id": "record-1"},
        "get_provision_record": {"id": "record-1"},
        "query_provision_records": [{"id": "record-1"}],
        "request_actor_provision": {
            "work_item": {"id": "work-2"},
            "provision_request": {"id": "request-2"},
        },
        "process_provision_work_item": {
            "work_item": {"id": "work-2", "status": "COMPLETED"},
            "provision_record": {"id": "record-2"},
            "reconciled": True,
        },
        "process_next_provision_work_item": {
            "work_item": {"id": "work-3", "status": "COMPLETED"},
            "provision_record": {"id": "record-3"},
            "reconciled": False,
        },
        "spawn_actor_runtime": {"attachment": {"id": "attachment-1"}},
        "stop_actor_runtime": None,
        "get_runtime_status": {"actor_id": "actor-1", "state": "ONLINE"},
        "reconcile_actor": True,
        "reconcile_all": 3,
        "get_work_item": {"id": "work-1"},
        "query_work_items": [{"id": "work-1"}],
        "get_domain_events": [{"id": "event-1"}],
        "can": {"allowed": True},
    })
    control = AsyncFacade({
        "open_session": {"id": "session-1"},
        "bind_session_thread": {"id": "session-1", "thread_id": "thread-1"},
        "get_session": {"id": "session-1"},
        "query_sessions": [{"id": "session-1"}],
        "register_binding": {"id": "binding-1"},
        "query_bindings": [{"id": "binding-1"}],
        "resolve_binding": {"id": "binding-1"},
        "lease_deliveries": [{"delivery": {"id": "delivery-1"}}],
        "ack_delivery": None,
        "fail_delivery": None,
        "list_pending_deliveries": [{"id": "delivery-1"}],
        "query_deliveries": [{"id": "delivery-1"}],
        "register_attachment": {"attachment": {"id": "attachment-1"}},
        "report_presence": {"presence": {"attachment_id": "attachment-1"}},
        "query_attachments": [{"id": "attachment-1"}],
        "get_presence": {"attachment_id": "attachment-1"},
    })
    server = GatewayServer(
        store=store,
        api=object(),
        manager=manager,
        bindings=object(),
        sessions=object(),
        deliveries=object(),
        application=application,
        control=control,
    )
    since = datetime.now(timezone.utc)
    service = FakeConnector("worker-1")

    server.register_managed_service(service)

    await server.start()
    await server.start()

    assert store.initialized == 1
    assert manager.started == 1
    assert manager.registered == ["worker-1"]

    assert await server.create_actor({"id": "actor-1"}) == {"actor": {"id": "actor-1"}}
    assert await server.get_actor("actor-1") == {"id": "actor-1"}
    assert await server.list_actors(kind="AGENT") == [{"id": "actor-1"}]
    assert await server.create_org_edge({"id": "edge-1"}) == {"org_edge": {"id": "edge-1"}}
    assert await server.list_org_edges(kind="REPORTS_TO") == [{"id": "edge-1"}]
    assert await server.issue_grant({"id": "grant-1"}) == {"grant": {"id": "grant-1"}}
    assert await server.list_grants("actor-1") == [{"id": "grant-1"}]
    assert await server.open_thread({"id": "thread-1"}) == {"thread": {"id": "thread-1"}}
    assert await server.get_thread("thread-1") == {"id": "thread-1"}
    assert await server.ingest({"payload": {"text": "hi"}}) == {"envelope": {"id": "env-1"}}
    assert await server.get_envelope("env-1") == {"id": "env-1"}
    assert await server.query_envelopes(thread_id="thread-1", sender_actor_id="actor-1", limit=2) == [{"id": "env-1"}]
    assert await server.create_work_item({"id": "work-1"}) == {"work_item": {"id": "work-1"}}
    assert await server.create_actor_profile({"id": "profile-1"}) == {"id": "profile-1"}
    assert await server.get_actor_profile("profile-1") == {"id": "profile-1"}
    assert await server.query_actor_profiles(archived=False, limit=4) == [{"id": "profile-1"}]
    assert await server.create_actor_control({"id": "control-1"}) == {"id": "control-1"}
    assert await server.get_actor_control("actor-1") == {"actor_id": "actor-1"}
    assert await server.query_actor_controls(manager_actor_id="manager-1", desired_state="ACTIVE", runtime_kind="LOCAL", limit=5) == [{"actor_id": "actor-1"}]
    assert await server.assign_actor_manager("actor-1", "manager-1") == {"actor_id": "actor-1", "manager_actor_id": "manager-1"}
    assert await server.set_actor_profile("actor-1", "profile-1") == {"actor_id": "actor-1", "profile_id": "profile-1"}
    assert await server.set_actor_desired_state("actor-1", "PAUSED") == {"actor_id": "actor-1", "desired_state": "PAUSED"}
    assert await server.submit_provision_request({"id": "request-1"}) == {"id": "request-1"}
    assert await server.get_provision_request("request-1") == {"id": "request-1"}
    assert await server.claim_provision_request("request-1", "executor-1") is True
    assert await server.query_provision_requests(status="PENDING", limit=6) == [{"id": "request-1"}]
    assert await server.execute_provision_request("request-1", executor_actor_id="executor-1") == {"id": "record-1"}
    assert await server.get_provision_record("record-1") == {"id": "record-1"}
    assert await server.query_provision_records(request_id="request-1", limit=7) == [{"id": "record-1"}]
    assert await server.request_actor_provision({"requested_by_actor_id": "actor-1"}) == {
        "work_item": {"id": "work-2"},
        "provision_request": {"id": "request-2"},
    }
    assert await server.process_provision_work_item(
        "work-2",
        executor_actor_id="executor-1",
        auto_reconcile=False,
    ) == {
        "work_item": {"id": "work-2", "status": "COMPLETED"},
        "provision_record": {"id": "record-2"},
        "reconciled": True,
    }
    assert await server.process_next_provision_work_item(
        owner_actor_id="factory-1",
        executor_actor_id="executor-1",
        auto_reconcile=False,
        scan_limit=6,
    ) == {
        "work_item": {"id": "work-3", "status": "COMPLETED"},
        "provision_record": {"id": "record-3"},
        "reconciled": False,
    }
    assert await server.spawn_actor_runtime("actor-1") == {"attachment": {"id": "attachment-1"}}
    assert await server.stop_actor_runtime("actor-1") is None
    assert await server.get_runtime_status("actor-1") == {"actor_id": "actor-1", "state": "ONLINE"}
    assert await server.reconcile_actor("actor-1") is True
    assert await server.reconcile_all(limit=8) == 3
    assert await server.get_work_item("work-1") == {"id": "work-1"}
    assert await server.query_work_items(owner_actor_id="actor-1", status="OPEN", limit=3) == [{"id": "work-1"}]
    assert await server.get_domain_events(since=since, limit=4) == [{"id": "event-1"}]
    assert await server.can("envelope.send", "actor-1", target_id="actor-2") == {"allowed": True}
    assert await server.open_session(surface="cli", actor_id="actor-1", external_key="cli:test") == {"id": "session-1"}
    assert await server.bind_session_thread("session-1", "thread-1") == {"id": "session-1", "thread_id": "thread-1"}
    assert await server.get_session("session-1") == {"id": "session-1"}
    assert await server.query_sessions(actor_id="actor-1", surface="cli", external_key="cli:test", state="OPEN", limit=5) == [{"id": "session-1"}]
    assert await server.register_binding({"id": "binding-1"}) == {"id": "binding-1"}
    assert await server.query_bindings(surface="cli", actor_id="actor-1", limit=6) == [{"id": "binding-1"}]
    assert await server.resolve_binding(surface="cli", external_key="cli:test", account_id="acc-1") == {"id": "binding-1"}
    assert await server.lease_deliveries(actor_id="actor-1", consumer_id="consumer-1", limit=7, lease_seconds=8) == [{"delivery": {"id": "delivery-1"}}]
    assert await server.ack_delivery("delivery-1") is None
    assert await server.fail_delivery("delivery-1", error={"msg": "x"}) is None
    assert await server.list_pending_deliveries("actor-1") == [{"id": "delivery-1"}]
    assert await server.query_deliveries(actor_id="actor-1", state="PENDING", limit=9) == [{"id": "delivery-1"}]
    assert await server.register_attachment({"id": "attachment-1"}) == {"attachment": {"id": "attachment-1"}}
    assert await server.report_presence({"attachment_id": "attachment-1"}) == {"presence": {"attachment_id": "attachment-1"}}
    assert await server.query_attachments(actor_id="actor-1", kind="TERMINAL", limit=10) == [{"id": "attachment-1"}]
    assert await server.get_presence("attachment-1") == {"attachment_id": "attachment-1"}

    await server.stop()

    assert manager.stopped == 1
    assert store.closed == 1
    assert server._started is False


async def test_v2_gateway_server_stop_closes_store_even_if_manager_fails() -> None:
    store = FakeStore()
    manager = FakeManager(stop_error=RuntimeError("stop failed"))
    server = GatewayServer(
        store=store,
        api=object(),
        manager=manager,
        bindings=object(),
        sessions=object(),
        deliveries=object(),
        application=AsyncFacade(),
        control=AsyncFacade(),
    )
    server._started = True

    with pytest.raises(RuntimeError, match="stop failed"):
        await server.stop()

    assert manager.stopped == 1
    assert store.closed == 1
    assert server._started is False
