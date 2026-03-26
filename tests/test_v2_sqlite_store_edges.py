from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import tyrant_core.store.sqlite_store as sqlite_store_module
from tyrant_core.models import (
    Actor,
    ActorBlueprint,
    ActorKind,
    ActorControl,
    ActorProfile,
    ActivationPolicy,
    Attachment,
    AttachmentKind,
    Binding,
    BindingThreadPolicy,
    DesiredState,
    Delivery,
    DeliveryState,
    DomainEvent,
    Envelope,
    Grant,
    GrantEffect,
    GrantScope,
    GrantScopeType,
    GrantSourceType,
    OrgEdge,
    OrgEdgeKind,
    Presence,
    PresenceStatus,
    ProvisionRecord,
    ProvisionRequest,
    ProvisionStatus,
    RuntimeKind,
    Session,
    SessionMode,
    SessionState,
    Thread,
    ThreadKind,
    WorkItem,
    WorkItemStatus,
)
from tyrant_core.store import SqliteStore
from tyrant_core.store.sqlite.delivery_repo import DeliveryRepository
from tyrant_core.store.sqlite.txn import SqliteSessionScope
from tyrant_core.store.base import noop_transaction
from tyrant_core.store.sqlite_store import (
    _default_database_url,
    _dt_to_str,
    _normalize_value,
    _row_to_model,
)


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _FakeMappingsResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeMappingsResult":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)


class _FakeWriteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)

    async def execute(self, _stmt):
        return self._responses.pop(0)


class _FakeScope:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def session(self) -> _FakeSession:
        return self._session

    async def upsert(self, *args, **kwargs) -> None:
        raise AssertionError("not expected in this test")


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, sql: str) -> None:
        self.calls.append(sql)

    def close(self) -> None:
        self.calls.append("close")


class _FakeDbapiConnection:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj


async def test_v2_sqlite_store_helper_functions_cover_normalization(tmp_path, monkeypatch) -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5)
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    model = GrantScope(type=GrantScopeType.ACTORS, targets=["actor-1"])

    class MappingRow:
        def __init__(self, payload: dict) -> None:
            self._mapping = payload

    assert _dt_to_str(None) is None
    assert _dt_to_str(naive) == "2026-01-02T03:04:05+00:00"
    assert _dt_to_str(aware) == "2026-01-02T03:04:05+00:00"
    assert _normalize_value(model) == {"type": "ACTORS", "targets": ["actor-1"]}
    assert _normalize_value(ActorKind.AGENT) == "AGENT"
    assert _normalize_value(naive) == "2026-01-02T03:04:05+00:00"
    assert _normalize_value([ActorKind.AGENT, naive]) == [
        "AGENT",
        "2026-01-02T03:04:05+00:00",
    ]
    assert _normalize_value({"kind": ActorKind.AGENT, "time": naive}) == {
        "kind": "AGENT",
        "time": "2026-01-02T03:04:05+00:00",
    }
    assert _row_to_model(Actor, None) is None
    assert _row_to_model(
        Actor,
        {
            "id": "actor-1",
            "kind": "AGENT",
            "name": "Actor",
            "slug": "actor-1",
            "org_role": None,
            "tags": [],
            "metadata": {},
            "created_at": _dt_to_str(_now()),
            "archived_at": None,
        },
    ).id == "actor-1"
    assert _row_to_model(
        Actor,
        MappingRow(
            {
                "id": "actor-2",
                "kind": "AGENT",
                "name": "Actor",
                "slug": "actor-2",
                "org_role": None,
                "tags": [],
                "metadata": {},
                "created_at": _dt_to_str(_now()),
                "archived_at": None,
            }
        ),
    ).id == "actor-2"

    monkeypatch.setattr(
        sqlite_store_module,
        "get_settings",
        lambda: type("Settings", (), {"data_dir": str(tmp_path / "data-dir")})(),
    )
    database_url = _default_database_url()
    assert database_url.endswith("data-dir/tyrant_core_v2.db")
    assert (tmp_path / "data-dir").is_dir()


async def test_v2_sqlite_session_scope_initialize_short_circuits_after_waiting(
    tmp_path,
) -> None:
    scope = SqliteSessionScope(database_url=_db_url(tmp_path, "scope.db"))

    await scope._initialize_lock.acquire()
    task = asyncio.create_task(scope.initialize())
    await asyncio.sleep(0)
    scope._initialized = True
    scope._initialize_lock.release()

    await task

    assert scope._initialized is True
    await scope.close()


async def test_v2_sqlite_session_scope_connect_listener_sets_pragmas(tmp_path) -> None:
    scope = SqliteSessionScope(database_url=_db_url(tmp_path, "pragma.db"))
    dbapi_conn = _FakeDbapiConnection()

    listener = next(
        fn
        for fn in scope._engine.sync_engine.pool.dispatch.connect
        if fn.__qualname__.endswith("SqliteSessionScope.__init__.<locals>._set_sqlite_pragma")
    )
    listener(dbapi_conn, None)

    assert dbapi_conn.cursor_obj.calls == [
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=5000",
        "PRAGMA foreign_keys=ON",
        "close",
    ]
    await scope.close()


async def test_v2_noop_transaction_is_usable() -> None:
    used = False

    async with noop_transaction():
        used = True

    assert used is True


async def test_v2_delivery_repo_skips_conflicted_lease_update() -> None:
    now = _now()
    repo = DeliveryRepository(
        _FakeScope(
            _FakeSession([
                _FakeMappingsResult([
                    {
                        "id": "delivery-1",
                        "envelope_id": "env-1",
                        "recipient_actor_id": "actor-1",
                        "target_attachment_id": None,
                        "state": "PENDING",
                        "attempt_count": 0,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "delivered_at": None,
                        "acked_at": None,
                        "next_retry_at": None,
                        "last_error": None,
                        "created_at": _dt_to_str(now),
                    }
                ]),
                _FakeWriteResult(0),
            ])
        )
    )

    leased = await repo.lease_deliveries(
        recipient_actor_id="actor-1",
        lease_owner="runtime-1",
        limit=1,
        lease_seconds=30,
    )

    assert leased == []


async def test_v2_sqlite_store_requires_transaction_and_supports_nested_transaction(
    tmp_path,
) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "nested.db"))

    with pytest.raises(RuntimeError, match="必须在 store.transaction"):
        await store.get_actor("actor-1")

    created_at = _now()
    async with store.transaction():
        await store.save_actor(
            Actor(
                id="actor-1",
                kind=ActorKind.AGENT,
                name="Actor",
                slug="actor-1",
                created_at=created_at,
            )
        )
        async with store.transaction():
            await store.update_actor("actor-1")
            await store.update_actor(
                "actor-1",
                archived_at=datetime(2026, 1, 2, 3, 4, 5),
                metadata={"note": "nested"},
            )
            actor = await store.get_actor("actor-1")
        assert actor is not None
        assert actor.archived_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        assert actor.metadata == {"note": "nested"}

    await store.close()


async def test_v2_sqlite_store_filters_and_resolution_edges(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "filters.db"))
    now = _now()

    async with store.transaction():
        await store.save_actor(
            Actor(
                id="actor-1",
                kind=ActorKind.AGENT,
                name="Actor 1",
                slug="actor-1",
                tags=["ops"],
                created_at=now,
            )
        )
        await store.save_actor(
            Actor(
                id="actor-2",
                kind=ActorKind.SERVICE,
                name="Actor 2",
                slug="actor-2",
                tags=["svc"],
                created_at=now + timedelta(seconds=1),
            )
        )
        await store.save_org_edge(
            OrgEdge(
                id="edge-1",
                kind=OrgEdgeKind.REPORTS_TO,
                src_actor_id="actor-1",
                dst_actor_id="actor-2",
                created_at=now,
            )
        )
        await store.save_grant(
            Grant(
                id="grant-active",
                subject_actor_id="actor-1",
                action="envelope.send",
                effect=GrantEffect.ALLOW,
                scope=GrantScope(type=GrantScopeType.GLOBAL),
                source_type=GrantSourceType.DIRECT,
                created_at=now,
            )
        )
        await store.save_grant(
            Grant(
                id="grant-expired",
                subject_actor_id="actor-1",
                action="envelope.send",
                effect=GrantEffect.ALLOW,
                scope=GrantScope(type=GrantScopeType.GLOBAL),
                source_type=GrantSourceType.DIRECT,
                expires_at=now - timedelta(seconds=1),
                created_at=now,
            )
        )
        await store.save_grant(
            Grant(
                id="grant-revoked",
                subject_actor_id="actor-1",
                action="envelope.send",
                effect=GrantEffect.ALLOW,
                scope=GrantScope(type=GrantScopeType.GLOBAL),
                source_type=GrantSourceType.DIRECT,
                created_at=now,
                revoked_at=now,
            )
        )
        await store.save_thread(
            Thread(
                id="thread-1",
                kind=ThreadKind.DIALOG,
                subject="demo",
                created_by="actor-1",
                participants=["actor-1", "actor-2"],
                created_at=now,
            )
        )
        await store.save_envelope(
            Envelope(
                id="env-1",
                thread_id="thread-1",
                sender_actor_id="actor-1",
                payload={"text": "hello"},
                created_at=now,
            )
        )
        await store.save_envelope(
            Envelope(
                id="env-2",
                thread_id="thread-1",
                sender_actor_id="actor-2",
                payload={"text": "world"},
                created_at=now + timedelta(seconds=1),
            )
        )
        await store.save_binding(
            Binding(
                id="binding-default",
                surface="cli",
                actor_id="actor-1",
                thread_policy=BindingThreadPolicy.REUSE,
                priority=1,
                created_at=now,
            )
        )
        await store.save_binding(
            Binding(
                id="binding-account",
                surface="cli",
                account_id="acc-1",
                actor_id="actor-2",
                thread_policy=BindingThreadPolicy.REUSE,
                priority=5,
                created_at=now + timedelta(seconds=1),
            )
        )
        await store.save_binding(
            Binding(
                id="binding-peer",
                surface="cli",
                peer="cli:alice",
                actor_id="actor-2",
                thread_policy=BindingThreadPolicy.REUSE,
                priority=10,
                created_at=now + timedelta(seconds=2),
            )
        )
        await store.save_binding(
            Binding(
                id="binding-disabled",
                surface="cli",
                peer="cli:alice",
                actor_id="actor-2",
                thread_policy=BindingThreadPolicy.REUSE,
                priority=99,
                created_at=now + timedelta(seconds=3),
                disabled_at=now,
            )
        )
        await store.save_session(
            Session(
                id="session-1",
                surface="cli",
                actor_id="actor-1",
                external_key="cli:alice",
                thread_id="thread-1",
                mode=SessionMode.PERSISTENT,
                state=SessionState.OPEN,
                created_at=now,
                last_seen_at=now,
            )
        )
        await store.save_session(
            Session(
                id="session-2",
                surface="cli",
                actor_id="actor-2",
                external_key="cli:bob",
                thread_id="thread-2",
                mode=SessionMode.EPHEMERAL,
                state=SessionState.CLOSED,
                created_at=now,
                last_seen_at=now + timedelta(seconds=5),
                closed_at=now + timedelta(seconds=5),
            )
        )
        await store.save_attachment(
            Attachment(
                id="attachment-1",
                actor_id="actor-1",
                kind=AttachmentKind.TERMINAL,
                endpoint={"transport": "pty"},
                capabilities=["stdin", "stdout"],
                created_at=now,
            )
        )
        await store.save_attachment(
            Attachment(
                id="attachment-2",
                actor_id="actor-2",
                kind=AttachmentKind.UI,
                endpoint={"transport": "cli"},
                created_at=now + timedelta(seconds=1),
            )
        )
        await store.save_presence(
            Presence(
                attachment_id="attachment-1",
                actor_id="actor-1",
                gateway_id="gw-1",
                status=PresenceStatus.ONLINE,
                leases=1,
                last_heartbeat_at=now,
            )
        )
        await store.save_presence(
            Presence(
                attachment_id="attachment-1",
                actor_id="actor-1",
                gateway_id="gw-2",
                status=PresenceStatus.BUSY,
                leases=2,
                last_heartbeat_at=now + timedelta(seconds=1),
            )
        )

        actors = await store.list_actors(kind="AGENT", nonsense="x")
        edges = await store.list_org_edges(kind="REPORTS_TO", nonsense="x")
        grants = await store.list_grants("actor-1")
        envelopes_by_thread = await store.query_envelopes(thread_id="thread-1")
        envelopes_by_sender = await store.query_envelopes(sender_actor_id="actor-2")
        bindings_by_surface = await store.query_bindings(surface="cli")
        bindings_by_actor = await store.query_bindings(actor_id="actor-2")
        exact_peer = await store.resolve_binding(surface="cli", peer="cli:alice")
        exact_account = await store.resolve_binding(surface="cli", account_id="acc-1")
        mismatch_account = await store.resolve_binding(surface="cli", account_id="acc-x")
        mismatch_peer = await store.resolve_binding(surface="cli", peer="cli:missing")
        fallback = await store.resolve_binding(surface="cli")
        sessions_by_actor = await store.query_sessions(actor_id="actor-1")
        sessions_by_surface = await store.query_sessions(surface="cli")
        sessions_by_external = await store.query_sessions(external_key="cli:bob")
        sessions_by_state = await store.query_sessions(state="CLOSED")
        attachments_by_actor = await store.query_attachments(actor_id="actor-1")
        attachments_by_kind = await store.query_attachments(kind="UI")
        presence = await store.get_presence("attachment-1")

    assert [actor.id for actor in actors] == ["actor-1"]
    assert [edge.id for edge in edges] == ["edge-1"]
    assert [grant.id for grant in grants] == ["grant-active"]
    assert [envelope.id for envelope in envelopes_by_thread] == ["env-2", "env-1"]
    assert [envelope.id for envelope in envelopes_by_sender] == ["env-2"]
    assert [binding.id for binding in bindings_by_surface] == [
        "binding-disabled",
        "binding-peer",
        "binding-account",
        "binding-default",
    ]
    assert [binding.id for binding in bindings_by_actor] == [
        "binding-disabled",
        "binding-peer",
        "binding-account",
    ]
    assert exact_peer is not None and exact_peer.id == "binding-peer"
    assert exact_account is not None and exact_account.id == "binding-account"
    assert mismatch_account is not None and mismatch_account.id == "binding-default"
    assert mismatch_peer is not None and mismatch_peer.id == "binding-default"
    assert fallback is not None and fallback.id == "binding-default"
    assert [session.id for session in sessions_by_actor] == ["session-1"]
    assert [session.id for session in sessions_by_surface] == ["session-2", "session-1"]
    assert [session.id for session in sessions_by_external] == ["session-2"]
    assert [session.id for session in sessions_by_state] == ["session-2"]
    assert [attachment.id for attachment in attachments_by_actor] == ["attachment-1"]
    assert [attachment.id for attachment in attachments_by_kind] == ["attachment-2"]
    assert presence is not None
    assert presence.gateway_id == "gw-2"
    assert presence.status == PresenceStatus.BUSY
    await store.close()


async def test_v2_sqlite_store_delivery_work_item_and_event_edges(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "edges.db"))
    now = _now()

    async with store.transaction():
        await store.save_delivery(
            Delivery(
                id="delivery-pending",
                envelope_id="env-pending",
                recipient_actor_id="worker",
                created_at=now,
            )
        )
        await store.save_delivery(
            Delivery(
                id="delivery-retry-later",
                envelope_id="env-future",
                recipient_actor_id="worker",
                state=DeliveryState.FAILED,
                next_retry_at=now + timedelta(hours=1),
                created_at=now + timedelta(seconds=1),
            )
        )
        await store.save_delivery(
            Delivery(
                id="delivery-expired-lease",
                envelope_id="env-expired",
                recipient_actor_id="worker",
                state=DeliveryState.LEASED,
                attempt_count=3,
                lease_owner="old",
                lease_expires_at=now - timedelta(seconds=1),
                created_at=now + timedelta(seconds=2),
            )
        )
        await store.save_delivery(
            Delivery(
                id="delivery-active-lease",
                envelope_id="env-active",
                recipient_actor_id="worker",
                state=DeliveryState.LEASED,
                attempt_count=1,
                lease_owner="current",
                lease_expires_at=now + timedelta(hours=1),
                created_at=now + timedelta(seconds=3),
            )
        )
        leased = await store.lease_deliveries(
            recipient_actor_id="worker",
            lease_owner="runtime-1",
            limit=10,
            lease_seconds=30,
        )
        deliveries_by_actor = await store.query_deliveries(recipient_actor_id="worker")
        deliveries_by_state = await store.query_deliveries(state="LEASED")

        await store.save_work_item(
            WorkItem(
                id="work-1",
                creator_actor_id="actor-1",
                owner_actor_id="worker",
                thread_id="thread-1",
                status=WorkItemStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
        )
        await store.update_work_item("work-1")
        await store.update_work_item(
            "work-1",
            status=WorkItemStatus.COMPLETED,
            result={"ok": True},
            closed_at=datetime(2026, 1, 2, 3, 4, 5),
        )
        work_item = await store.get_work_item("work-1")
        missing_work_item = await store.get_work_item("missing")
        work_items_by_owner = await store.query_work_items(owner_actor_id="worker")
        work_items_by_status = await store.query_work_items(status="COMPLETED")

        await store.append_domain_event(
            DomainEvent(
                id="event-1",
                type="actor.created",
                source="test",
                payload={"id": "actor-1"},
                created_at=now,
            )
        )
        await store.append_domain_event(
            DomainEvent(
                id="event-2",
                type="actor.updated",
                source="test",
                payload={"id": "actor-1"},
                created_at=now + timedelta(seconds=5),
            )
        )
        all_events = await store.get_domain_events(limit=10)
        recent_events = await store.get_domain_events(since=now + timedelta(seconds=1), limit=10)

    assert [delivery.id for delivery in leased] == [
        "delivery-pending",
        "delivery-expired-lease",
    ]
    assert leased[0].attempt_count == 1
    assert leased[1].attempt_count == 4
    assert {delivery.id for delivery in deliveries_by_actor} == {
        "delivery-active-lease",
        "delivery-expired-lease",
        "delivery-retry-later",
        "delivery-pending",
    }
    assert {delivery.id for delivery in deliveries_by_state} == {
        "delivery-active-lease",
        "delivery-expired-lease",
        "delivery-pending",
    }
    assert work_item is not None
    assert work_item.status == WorkItemStatus.COMPLETED
    assert work_item.result == {"ok": True}
    assert work_item.closed_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert missing_work_item is None
    assert [item.id for item in work_items_by_owner] == ["work-1"]
    assert [item.id for item in work_items_by_status] == ["work-1"]
    assert [event.id for event in all_events] == ["event-2", "event-1"]
    assert [event.id for event in recent_events] == ["event-2"]
    await store.close()


async def test_v2_sqlite_store_claim_work_item_is_atomic_and_owner_scoped(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "claim-work-item.db"))
    now = _now()

    async with store.transaction():
        await store.save_work_item(
            WorkItem(
                id="work-1",
                kind="PROVISION_ACTOR",
                creator_actor_id="creator-1",
                owner_actor_id="factory-a",
                thread_id="thread-1",
                status=WorkItemStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
        )
        await store.save_work_item(
            WorkItem(
                id="work-2",
                kind="PROVISION_ACTOR",
                creator_actor_id="creator-1",
                owner_actor_id="factory-b",
                thread_id="thread-1",
                status=WorkItemStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
        )
        first_claim = await store.claim_work_item("work-1", owner_actor_id="factory-a")
        second_claim = await store.claim_work_item("work-1", owner_actor_id="factory-a")
        wrong_owner_claim = await store.claim_work_item("work-2", owner_actor_id="factory-a")
        claimed_item = await store.get_work_item("work-1")
        untouched_item = await store.get_work_item("work-2")

    assert first_claim is True
    assert second_claim is False
    assert wrong_owner_claim is False
    assert claimed_item is not None
    assert claimed_item.status == WorkItemStatus.IN_PROGRESS
    assert claimed_item.updated_at >= now
    assert untouched_item is not None
    assert untouched_item.status == WorkItemStatus.PENDING
    await store.close()


async def test_v2_sqlite_store_management_and_provisioning_edges(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "management-provisioning-edges.db"))
    now = _now()

    async with store.transaction():
        await store.save_actor_profile(
            ActorProfile(
                id="profile-1",
                name="worker-profile",
                instruction="work hard",
                toolsets=["terminal"],
                runtime_kind=RuntimeKind.LOCAL,
                created_at=now,
            )
        )
        await store.save_actor_profile(
            ActorProfile(
                id="profile-2",
                name="archived-profile",
                runtime_kind=RuntimeKind.REMOTE,
                created_at=now + timedelta(seconds=1),
                archived_at=now + timedelta(seconds=2),
            )
        )
        await store.save_actor_control(
            ActorControl(
                id="control-1",
                actor_id="worker-1",
                manager_actor_id="manager-1",
                profile_id="profile-1",
                desired_state=DesiredState.ACTIVE,
                activation_policy=ActivationPolicy.AUTO_START,
                runtime_kind=RuntimeKind.LOCAL,
                updated_at=now,
            )
        )
        await store.update_actor_control(
            "worker-1",
            desired_state=DesiredState.PAUSED,
            runtime_kind=RuntimeKind.TERMINAL,
            updated_at=now + timedelta(seconds=3),
        )

        await store.save_provision_request(
            ProvisionRequest(
                id="request-pending",
                requested_by_actor_id="manager-1",
                target_parent_actor_id="manager-1",
                manager_actor_id="manager-1",
                profile_id="profile-1",
                status=ProvisionStatus.PENDING,
                idempotency_key="spawn-worker-1",
                blueprint=ActorBlueprint(
                    name="worker-1",
                    parent_actor_id="manager-1",
                    profile_id="profile-1",
                    manager_actor_id="manager-1",
                ),
                created_at=now,
            )
        )
        await store.save_provision_request(
            ProvisionRequest(
                id="request-completed",
                requested_by_actor_id="manager-1",
                target_parent_actor_id="manager-1",
                status=ProvisionStatus.SUCCEEDED,
                blueprint=ActorBlueprint(
                    name="worker-2",
                    parent_actor_id="manager-1",
                ),
                created_at=now + timedelta(seconds=1),
                closed_at=now + timedelta(seconds=2),
            )
        )
        first_claim = await store.claim_provision_request("request-pending", "executor-1")
        second_claim = await store.claim_provision_request("request-pending", "executor-2")
        claimed_request = await store.get_provision_request("request-pending")
        pending_requests = await store.query_provision_requests(status="CLAIMED")

        await store.save_provision_record(
            ProvisionRecord(
                id="record-1",
                request_id="request-pending",
                provisioner_name="template",
                executor_actor_id="executor-1",
                produced_actor_id="worker-1",
                control_id="control-1",
                state=ProvisionStatus.RUNNING,
                created_at=now,
            )
        )
        await store.update_provision_record(
            "record-1",
            state=ProvisionStatus.SUCCEEDED,
            finished_at=now + timedelta(seconds=4),
            notes={"bootstrap": "queued"},
        )
        record = await store.get_provision_record("record-1")
        records_by_request = await store.query_provision_records(request_id="request-pending")
        profiles_active = await store.query_actor_profiles(archived=False)
        profiles_archived = await store.query_actor_profiles(archived=True)
        control = await store.get_actor_control("worker-1")
        controls = await store.query_actor_controls(
            manager_actor_id="manager-1",
            desired_state="PAUSED",
            runtime_kind="TERMINAL",
        )

    assert first_claim is True
    assert second_claim is False
    assert claimed_request is not None
    assert claimed_request.status == ProvisionStatus.CLAIMED
    assert claimed_request.claimed_by_actor_id == "executor-1"
    assert [item.id for item in pending_requests] == ["request-pending"]
    assert [item.id for item in profiles_active] == ["profile-1"]
    assert [item.id for item in profiles_archived] == ["profile-2"]
    assert control is not None
    assert control.desired_state == DesiredState.PAUSED
    assert control.runtime_kind == RuntimeKind.TERMINAL
    assert [item.actor_id for item in controls] == ["worker-1"]
    assert record is not None
    assert record.state == ProvisionStatus.SUCCEEDED
    assert record.notes == {"bootstrap": "queued"}
    assert [item.id for item in records_by_request] == ["record-1"]
    await store.close()
