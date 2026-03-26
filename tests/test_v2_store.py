from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tyrant_core.models import (
    Actor,
    ActorBlueprint,
    ActorKind,
    ActorControl,
    ActorProfile,
    ActivationPolicy,
    Binding,
    BindingThreadPolicy,
    DesiredState,
    Delivery,
    DeliveryState,
    Envelope,
    ProvisionRecord,
    ProvisionRequest,
    ProvisionStatus,
    RuntimeKind,
    Session,
    SessionMode,
    SessionState,
    Thread,
    ThreadKind,
)
from tyrant_core.store import SqliteStore


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def test_v2_sqlite_store_persists_core_entities(tmp_path) -> None:
    database_url = _db_url(tmp_path, "v2-store.db")
    now = datetime.now(timezone.utc)

    store = SqliteStore(database_url=database_url)
    async with store.transaction():
        await store.save_actor(
            Actor(
                id="tyrant",
                kind=ActorKind.TYRANT,
                name="Tyrant",
                slug="tyrant",
                tags=["ops", "root"],
                created_at=now,
            )
        )
        await store.save_thread(
            Thread(
                id="thread-1",
                kind=ThreadKind.DIALOG,
                subject="cli:demo",
                created_by="tyrant",
                participants=["tyrant", "worker"],
                created_at=now,
            )
        )
        await store.save_envelope(
            Envelope(
                id="env-1",
                thread_id="thread-1",
                sender_actor_id="tyrant",
                payload={"text": "hello"},
                created_at=now,
            )
        )
        await store.save_binding(
            Binding(
                id="binding-1",
                surface="cli",
                peer="cli:demo",
                actor_id="tyrant",
                thread_policy=BindingThreadPolicy.REUSE,
                created_at=now,
            )
        )
        await store.save_session(
            Session(
                id="session-1",
                surface="cli",
                binding_id="binding-1",
                actor_id="tyrant",
                external_key="cli:demo",
                thread_id="thread-1",
                mode=SessionMode.PERSISTENT,
                state=SessionState.OPEN,
                metadata={"source": "test"},
                created_at=now,
                last_seen_at=now,
            )
        )
    await store.close()

    reopened = SqliteStore(database_url=database_url)
    async with reopened.transaction():
        actors = await reopened.list_actors(tags=["ops"])
        thread = await reopened.get_thread("thread-1")
        envelope = await reopened.get_envelope("env-1")
        sessions = await reopened.query_sessions(
            surface="cli",
            external_key="cli:demo",
        )
        binding = await reopened.resolve_binding(surface="cli", peer="cli:demo")

    assert [actor.id for actor in actors] == ["tyrant"]
    assert thread is not None
    assert thread.participants == ["tyrant", "worker"]
    assert envelope is not None
    assert envelope.payload["text"] == "hello"
    assert len(sessions) == 1
    assert sessions[0].binding_id == "binding-1"
    assert sessions[0].thread_id == "thread-1"
    assert binding is not None
    assert binding.actor_id == "tyrant"
    await reopened.close()


async def test_v2_sqlite_store_delivery_state_machine(tmp_path) -> None:
    database_url = _db_url(tmp_path, "v2-delivery.db")
    now = datetime.now(timezone.utc)

    store = SqliteStore(database_url=database_url)
    async with store.transaction():
        await store.save_delivery(
            Delivery(
                id="delivery-1",
                envelope_id="env-1",
                recipient_actor_id="worker",
                created_at=now,
            )
        )
        await store.save_delivery(
            Delivery(
                id="delivery-2",
                envelope_id="env-2",
                recipient_actor_id="worker",
                state=DeliveryState.FAILED,
                last_error={"error": "boom"},
                created_at=now + timedelta(seconds=1),
            )
        )

    async with store.transaction():
        leased = await store.lease_deliveries(
            recipient_actor_id="worker",
            lease_owner="runtime-a",
            limit=2,
            lease_seconds=5,
        )

    assert [delivery.id for delivery in leased] == ["delivery-1", "delivery-2"]
    assert all(delivery.state == DeliveryState.LEASED for delivery in leased)
    assert all(delivery.attempt_count == 1 for delivery in leased)

    async with store.transaction():
        await store.fail_delivery("delivery-1", error={"error": "temporary"})
        await store.ack_delivery("delivery-2")

    async with store.transaction():
        failed = await store.get_delivery("delivery-1")
        acked = await store.get_delivery("delivery-2")
        re_leased = await store.lease_deliveries(
            recipient_actor_id="worker",
            lease_owner="runtime-b",
            limit=1,
            lease_seconds=5,
        )

    assert failed is not None
    assert failed.state == DeliveryState.FAILED
    assert failed.last_error == {"error": "temporary"}
    assert acked is not None
    assert acked.state == DeliveryState.ACKED
    assert acked.acked_at is not None
    assert [delivery.id for delivery in re_leased] == ["delivery-1"]
    assert re_leased[0].attempt_count == 2
    await store.close()


async def test_v2_sqlite_store_binding_resolution_prefers_specific_match(tmp_path) -> None:
    database_url = _db_url(tmp_path, "v2-bindings.db")
    now = datetime.now(timezone.utc)

    store = SqliteStore(database_url=database_url)
    async with store.transaction():
        await store.save_binding(
            Binding(
                id="binding-default",
                surface="cli",
                actor_id="default-actor",
                thread_policy=BindingThreadPolicy.REUSE,
                priority=0,
                created_at=now,
            )
        )
        await store.save_binding(
            Binding(
                id="binding-specific",
                surface="cli",
                peer="cli:alice",
                actor_id="alice-actor",
                thread_policy=BindingThreadPolicy.REUSE,
                priority=10,
                created_at=now,
            )
        )

        exact = await store.resolve_binding(surface="cli", peer="cli:alice")
        fallback = await store.resolve_binding(surface="cli", peer="cli:bob")
        missing = await store.resolve_binding(surface="ws")
        bindings = await store.query_bindings(surface="cli")

    assert exact is not None
    assert exact.id == "binding-specific"
    assert fallback is not None
    assert fallback.id == "binding-default"
    assert missing is None
    assert [binding.id for binding in bindings] == [
        "binding-specific",
        "binding-default",
    ]
    await store.close()


async def test_v2_sqlite_store_persists_management_and_provisioning_entities(
    tmp_path,
) -> None:
    database_url = _db_url(tmp_path, "v2-management-provisioning.db")
    now = datetime.now(timezone.utc)

    store = SqliteStore(database_url=database_url)
    async with store.transaction():
        await store.save_actor(
            Actor(
                id="manager",
                kind=ActorKind.AGENT,
                name="Manager",
                slug="manager",
                created_at=now,
            )
        )
        await store.save_actor_profile(
            ActorProfile(
                id="profile-1",
                name="researcher",
                instruction="collect facts",
                toolsets=["web", "files"],
                runtime_kind=RuntimeKind.LOCAL,
                created_at=now,
            )
        )
        await store.save_actor_control(
            ActorControl(
                id="control-1",
                actor_id="manager",
                manager_actor_id="tyrant",
                profile_id="profile-1",
                desired_state=DesiredState.ACTIVE,
                activation_policy=ActivationPolicy.AUTO_START,
                runtime_kind=RuntimeKind.LOCAL,
                updated_at=now,
            )
        )
        await store.save_provision_request(
            ProvisionRequest(
                id="request-1",
                requested_by_actor_id="tyrant",
                target_parent_actor_id="manager",
                manager_actor_id="tyrant",
                profile_id="profile-1",
                status=ProvisionStatus.PENDING,
                blueprint=ActorBlueprint(
                    name="worker-1",
                    parent_actor_id="manager",
                    profile_id="profile-1",
                    manager_actor_id="tyrant",
                    tags=["research"],
                ),
                created_at=now,
            )
        )
        await store.save_provision_record(
            ProvisionRecord(
                id="record-1",
                request_id="request-1",
                provisioner_name="template",
                executor_actor_id="agent-factory",
                produced_actor_id="worker-1",
                control_id="control-1",
                state=ProvisionStatus.RUNNING,
                created_at=now,
            )
        )
    await store.close()

    reopened = SqliteStore(database_url=database_url)
    async with reopened.transaction():
        profile = await reopened.get_actor_profile("profile-1")
        profiles = await reopened.query_actor_profiles(archived=False)
        control = await reopened.get_actor_control("manager")
        controls = await reopened.query_actor_controls(manager_actor_id="tyrant")
        request = await reopened.get_provision_request("request-1")
        requests = await reopened.query_provision_requests(status="PENDING")
        record = await reopened.get_provision_record("record-1")
        records = await reopened.query_provision_records(request_id="request-1")

    assert profile is not None
    assert profile.toolsets == ["web", "files"]
    assert [item.id for item in profiles] == ["profile-1"]
    assert control is not None
    assert control.profile_id == "profile-1"
    assert [item.actor_id for item in controls] == ["manager"]
    assert request is not None
    assert request.blueprint.name == "worker-1"
    assert [item.id for item in requests] == ["request-1"]
    assert record is not None
    assert record.provisioner_name == "template"
    assert [item.id for item in records] == ["record-1"]
    await reopened.close()
