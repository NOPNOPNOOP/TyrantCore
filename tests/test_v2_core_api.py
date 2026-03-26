from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tyrant_core.api import SystemAPI
from tyrant_core.api.policies import PolicyError
from tyrant_core.management import ActorReconciler
from tyrant_core.models import (
    Attachment,
    AttachmentKind,
    PolicyDeniedError,
    Presence,
    PresenceStatus,
)
from tyrant_core.runtime.driver_registry import build_single_runtime_driver_registry
from tyrant_core.runtime.runtime_driver import RuntimeSpawnResult
from tyrant_core.runtime.runtime_supervisor import RuntimeSupervisor
from tyrant_core.store import SqliteStore


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


class _FakeDriver:
    async def spawn(self, *, actor_id: str, control, profile=None) -> RuntimeSpawnResult:
        now = datetime.now(timezone.utc)
        return RuntimeSpawnResult(
            attachment=Attachment(
                id=f"attachment:{actor_id}",
                actor_id=actor_id,
                kind=AttachmentKind.LOCAL_RUNTIME,
                endpoint={"driver": "fake"},
                capabilities=["reply"],
                created_at=now,
            ),
            presence=Presence(
                attachment_id=f"attachment:{actor_id}",
                actor_id=actor_id,
                gateway_id="gateway:test",
                status=PresenceStatus.ONLINE,
                leases=0,
                last_heartbeat_at=now,
            ),
        )

    async def stop(self, *, actor_id: str) -> None:
        return None

    async def status(self, *, actor_id: str) -> dict | None:
        return {"actor_id": actor_id, "driver": "fake"}


async def test_v2_system_api_exposes_core_objects(tmp_path) -> None:
    api = SystemAPI(store=SqliteStore(database_url=_db_url(tmp_path, "core-api.db")))

    await api.create_actor({
        "id": "tyrant",
        "kind": "TYRANT",
        "name": "Tyrant",
        "slug": "tyrant",
        "bypass_policy": True,
    })
    await api.create_actor({
        "id": "worker",
        "kind": "AGENT",
        "name": "Worker",
        "slug": "worker",
        "caller_actor_id": "tyrant",
    })

    edge = await api.create_org_edge({
        "id": "edge-1",
        "kind": "REPORTS_TO",
        "src_actor_id": "tyrant",
        "dst_actor_id": "worker",
        "caller_actor_id": "tyrant",
    })
    grant = await api.issue_grant({
        "id": "grant-1",
        "subject_actor_id": "worker",
        "action": "envelope.send",
        "scope": {"type": "ACTORS", "targets": ["tyrant"]},
        "caller_actor_id": "tyrant",
    })
    attachment = await api.register_attachment({
        "id": "attachment-1",
        "actor_id": "worker",
        "kind": "LOCAL_RUNTIME",
        "endpoint": {"transport": "local"},
        "capabilities": ["reply"],
        "caller_actor_id": "tyrant",
    })
    presence = await api.report_presence({
        "attachment_id": "attachment-1",
        "actor_id": "worker",
        "gateway_id": "gateway:test",
        "status": "ONLINE",
        "leases": 2,
        "caller_actor_id": "tyrant",
    })
    work_item = await api.create_work_item({
        "id": "work-1",
        "creator_actor_id": "tyrant",
        "owner_actor_id": "worker",
        "subject": "demo",
        "caller_actor_id": "tyrant",
    })
    explicit_work_item = await api.create_work_item({
        "id": "work-2",
        "creator_actor_id": "tyrant",
        "owner_actor_id": "worker",
        "thread_id": "thread-explicit",
        "caller_actor_id": "tyrant",
    })

    actors = await api.list_actors()
    edges = await api.list_org_edges()
    grants = await api.list_grants("worker")
    attachments = await api.query_attachments(actor_id="worker")
    fetched_presence = await api.get_presence("attachment-1")
    items = await api.query_work_items(owner_actor_id="worker")
    events = await api.get_domain_events(limit=20)

    assert edge["org_edge"]["id"] == "edge-1"
    assert grant["grant"]["id"] == "grant-1"
    assert attachment["attachment"]["id"] == "attachment-1"
    assert presence["presence"]["status"] == "ONLINE"
    assert work_item["work_item"]["id"] == "work-1"
    assert explicit_work_item["work_item"]["thread_id"] == "thread-explicit"
    assert {actor["id"] for actor in actors} == {"tyrant", "worker"}
    assert [item["id"] for item in edges] == ["edge-1"]
    assert [item["id"] for item in grants] == ["grant-1"]
    assert [item["id"] for item in attachments] == ["attachment-1"]
    assert fetched_presence is not None
    assert fetched_presence["leases"] == 2
    assert [item["id"] for item in items] == ["work-2", "work-1"]
    assert {event["type"] for event in events} >= {
        "org_edge.created",
        "grant.issued",
        "attachment.registered",
        "presence.reported",
        "work_item.created",
    }


async def test_v2_policy_engine_supports_subtree_and_deny_precedence(tmp_path) -> None:
    api = SystemAPI(store=SqliteStore(database_url=_db_url(tmp_path, "policy.db")))

    for actor_id, kind in [
        ("tyrant", "TYRANT"),
        ("lead", "AGENT"),
        ("worker", "AGENT"),
        ("outsider", "AGENT"),
    ]:
        await api.create_actor({
            "id": actor_id,
            "kind": kind,
            "name": actor_id.title(),
            "slug": actor_id,
            "bypass_policy": actor_id == "tyrant",
            "caller_actor_id": None if actor_id == "tyrant" else "tyrant",
        })

    await api.create_org_edge({
        "kind": "REPORTS_TO",
        "src_actor_id": "lead",
        "dst_actor_id": "worker",
        "caller_actor_id": "tyrant",
    })
    await api.issue_grant({
        "subject_actor_id": "outsider",
        "action": "envelope.send",
        "scope": {"type": "SUBTREE", "targets": ["lead"]},
        "caller_actor_id": "tyrant",
    })
    await api.issue_grant({
        "subject_actor_id": "outsider",
        "action": "thread.open",
        "scope": {"type": "SUBTREE", "targets": ["lead"]},
        "caller_actor_id": "tyrant",
    })

    allowed = await api.can("envelope.send", "outsider", target_id="worker")
    assert allowed.allowed is True
    assert allowed.reason == "explicit_allow"

    await api.send_envelope({
        "sender_actor_id": "outsider",
        "recipient_actor_id": "lead",
        "payload": {"text": "allowed"},
        "caller_actor_id": "outsider",
    })

    await api.issue_grant({
        "subject_actor_id": "outsider",
        "action": "envelope.send",
        "effect": "DENY",
        "scope": {"type": "ACTORS", "targets": ["worker"]},
        "caller_actor_id": "tyrant",
    })

    denied = await api.can("envelope.send", "outsider", target_id="worker")
    assert denied.allowed is False
    assert denied.reason == "explicit_deny"

    with pytest.raises(PolicyDeniedError):
        await api.send_envelope({
            "sender_actor_id": "outsider",
            "recipient_actor_id": "worker",
            "payload": {"text": "blocked"},
            "caller_actor_id": "outsider",
        })


async def test_v2_system_api_exposes_management_and_provisioning(tmp_path) -> None:
    store = SqliteStore(database_url=_db_url(tmp_path, "management-provisioning-api.db"))
    supervisor = RuntimeSupervisor(
        build_single_runtime_driver_registry(_FakeDriver()),
        store=store,
    )
    reconciler = ActorReconciler(supervisor, store=store)
    api = SystemAPI(
        store=store,
        runtime_supervisor=supervisor,
        reconciler=reconciler,
    )

    await api.create_actor({
        "id": "tyrant",
        "kind": "TYRANT",
        "name": "Tyrant",
        "slug": "tyrant",
        "bypass_policy": True,
    })
    await api.create_actor({
        "id": "manager",
        "kind": "AGENT",
        "name": "Manager",
        "slug": "manager",
        "caller_actor_id": "tyrant",
    })

    profile = await api.create_actor_profile({
        "id": "profile-1",
        "name": "research-worker",
        "instruction": "collect evidence",
        "toolsets": ["terminal"],
    })
    request = await api.submit_provision_request({
        "id": "request-1",
        "requested_by_actor_id": "tyrant",
        "manager_actor_id": "tyrant",
        "profile_id": "profile-1",
        "blueprint": {
            "name": "Worker One",
            "parent_actor_id": "manager",
            "profile_id": "profile-1",
            "manager_actor_id": "tyrant",
            "metadata": {
                "actor_id": "worker-1",
                "slug": "worker_1",
            },
        },
    })
    claimed = await api.claim_provision_request("request-1", "factory")
    record = await api.execute_provision_request(
        "request-1",
        executor_actor_id="factory",
    )
    control = await api.get_actor_control("worker-1")
    requests = await api.query_provision_requests(status="SUCCEEDED")
    records = await api.query_provision_records(request_id="request-1")
    actors = await api.list_actors(kind="AGENT")
    requested = await api.request_actor_provision({
        "requested_by_actor_id": "tyrant",
        "work_item_owner_actor_id": "tyrant",
        "subject": "spawn worker two",
        "caller_actor_id": "tyrant",
        "blueprint": {
            "name": "Worker Two",
            "parent_actor_id": "manager",
            "profile_id": "profile-1",
            "manager_actor_id": "tyrant",
            "metadata": {
                "actor_id": "worker-2",
                "slug": "worker_2",
            },
        },
    })
    processed = await api.process_provision_work_item(
        requested["work_item"]["id"],
        executor_actor_id="factory",
    )
    runtime_status = await api.get_runtime_status("worker-2")
    requested_next = await api.request_actor_provision({
        "requested_by_actor_id": "tyrant",
        "work_item_owner_actor_id": "tyrant",
        "subject": "spawn worker three",
        "caller_actor_id": "tyrant",
        "blueprint": {
            "name": "Worker Three",
            "parent_actor_id": "manager",
            "profile_id": "profile-1",
            "manager_actor_id": "tyrant",
            "metadata": {
                "actor_id": "worker-3",
                "slug": "worker_3",
            },
        },
    })
    processed_next = await api.process_next_provision_work_item(
        owner_actor_id="tyrant",
        executor_actor_id="factory",
    )
    runtime_status_next = await api.get_runtime_status("worker-3")
    control_two = await api.set_actor_desired_state("worker-2", "PAUSED")

    assert profile["id"] == "profile-1"
    assert request["id"] == "request-1"
    assert claimed is True
    assert record["produced_actor_id"] == "worker-1"
    assert control is not None
    assert control["manager_actor_id"] == "tyrant"
    assert [item["id"] for item in requests] == ["request-1"]
    assert [item["id"] for item in records] == [record["id"]]
    assert requested["work_item"]["kind"] == "PROVISION_ACTOR"
    assert requested["provision_request"]["work_item_id"] == requested["work_item"]["id"]
    assert processed["provision_record"]["produced_actor_id"] == "worker-2"
    assert processed["reconciled"] is True
    assert runtime_status == {"actor_id": "worker-2", "driver": "fake"}
    assert requested_next["work_item"]["kind"] == "PROVISION_ACTOR"
    assert processed_next is not None
    assert processed_next["provision_record"]["produced_actor_id"] == "worker-3"
    assert runtime_status_next == {"actor_id": "worker-3", "driver": "fake"}
    assert control_two["desired_state"] == "PAUSED"
    assert {item["id"] for item in actors} >= {"manager", "worker-1"}


async def test_v2_command_api_requires_explicit_policy_context(tmp_path) -> None:
    api = SystemAPI(store=SqliteStore(database_url=_db_url(tmp_path, "policy-context.db")))

    with pytest.raises(PolicyError):
        await api.create_actor({
            "id": "worker",
            "kind": "AGENT",
            "name": "Worker",
            "slug": "worker",
        })
