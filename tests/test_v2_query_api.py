from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from tyrant_core.api.query_api import QueryAPI
from tyrant_core.models import (
    ActorNotFoundError,
    DeliveryNotFoundError,
    EnvelopeNotFoundError,
    ThreadNotFoundError,
    WorkItemNotFoundError,
)


class FakeModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        assert mode == "json"
        return dict(self.payload)


class FakeQueryStore:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.actor = FakeModel({"id": "actor-1", "created_at": now})
        self.thread = FakeModel({"id": "thread-1"})
        self.envelope = FakeModel({"id": "env-1"})
        self.delivery = FakeModel({"id": "delivery-1"})
        self.work_item = FakeModel({"id": "work-1"})
        self.calls: list[tuple[str, object]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def get_actor(self, actor_id: str):
        self.calls.append(("get_actor", actor_id))
        return self.actor

    async def list_actors(self, **filters):
        self.calls.append(("list_actors", dict(filters)))
        return [FakeModel({"id": "actor-1"}), FakeModel({"id": "actor-2"})]

    async def list_org_edges(self, **filters):
        self.calls.append(("list_org_edges", dict(filters)))
        return [FakeModel({"id": "edge-1"})]

    async def list_grants(self, subject_actor_id: str):
        self.calls.append(("list_grants", subject_actor_id))
        return [FakeModel({"id": "grant-1"})]

    async def get_thread(self, thread_id: str):
        self.calls.append(("get_thread", thread_id))
        return self.thread

    async def get_envelope(self, envelope_id: str):
        self.calls.append(("get_envelope", envelope_id))
        return self.envelope

    async def query_envelopes(self, **kwargs):
        self.calls.append(("query_envelopes", dict(kwargs)))
        return [FakeModel({"id": "env-1"}), FakeModel({"id": "env-2"})]

    async def get_delivery(self, delivery_id: str):
        self.calls.append(("get_delivery", delivery_id))
        return self.delivery

    async def query_deliveries(self, **kwargs):
        self.calls.append(("query_deliveries", dict(kwargs)))
        return [FakeModel({"id": "delivery-1"}), FakeModel({"id": "delivery-2"})]

    async def get_work_item(self, work_item_id: str):
        self.calls.append(("get_work_item", work_item_id))
        return self.work_item

    async def query_work_items(self, **kwargs):
        self.calls.append(("query_work_items", dict(kwargs)))
        return [FakeModel({"id": "work-1"})]

    async def query_sessions(self, **kwargs):
        self.calls.append(("query_sessions", dict(kwargs)))
        return [FakeModel({"id": "session-1"})]

    async def query_attachments(self, **kwargs):
        self.calls.append(("query_attachments", dict(kwargs)))
        return [FakeModel({"id": "attachment-1"})]

    async def get_presence(self, attachment_id: str):
        self.calls.append(("get_presence", attachment_id))
        return FakeModel({"attachment_id": attachment_id, "status": "ONLINE"})

    async def get_domain_events(self, **kwargs):
        self.calls.append(("get_domain_events", dict(kwargs)))
        return [FakeModel({"id": "event-1"})]


async def test_v2_query_api_happy_path_and_filter_passthrough() -> None:
    store = FakeQueryStore()
    api = QueryAPI(store)
    since = datetime.now(timezone.utc)

    actor = await api.get_actor("actor-1")
    actors = await api.list_actors(kind="AGENT")
    edges = await api.list_org_edges(kind="REPORTS_TO")
    grants = await api.list_grants("actor-1")
    thread = await api.get_thread("thread-1")
    envelope = await api.get_envelope("env-1")
    envelopes = await api.query_envelopes(thread_id="thread-1", sender_actor_id="actor-1", limit=2)
    delivery = await api.get_delivery("delivery-1")
    pending = await api.list_pending_deliveries("actor-1")
    deliveries = await api.query_deliveries(actor_id="actor-1", state="ACKED", limit=2)
    work_item = await api.get_work_item("work-1")
    work_items = await api.query_work_items(owner_actor_id="actor-1", status="RUNNING", limit=3)
    sessions = await api.query_sessions(
        actor_id="actor-1",
        surface="cli",
        external_key="ext-1",
        state="OPEN",
        limit=4,
    )
    attachments = await api.query_attachments(actor_id="actor-1", kind="TERMINAL", limit=5)
    presence = await api.get_presence("attachment-1")
    events = await api.get_domain_events(since=since, limit=6)

    assert actor["id"] == "actor-1"
    assert actors == [{"id": "actor-1"}, {"id": "actor-2"}]
    assert edges == [{"id": "edge-1"}]
    assert grants == [{"id": "grant-1"}]
    assert thread == {"id": "thread-1"}
    assert envelope == {"id": "env-1"}
    assert envelopes == [{"id": "env-1"}, {"id": "env-2"}]
    assert delivery == {"id": "delivery-1"}
    assert pending == [{"id": "delivery-1"}, {"id": "delivery-2"}]
    assert deliveries == [{"id": "delivery-1"}, {"id": "delivery-2"}]
    assert work_item == {"id": "work-1"}
    assert work_items == [{"id": "work-1"}]
    assert sessions == [{"id": "session-1"}]
    assert attachments == [{"id": "attachment-1"}]
    assert presence == {"attachment_id": "attachment-1", "status": "ONLINE"}
    assert events == [{"id": "event-1"}]
    assert ("list_actors", {"kind": "AGENT"}) in store.calls
    assert ("list_org_edges", {"kind": "REPORTS_TO"}) in store.calls
    assert ("list_grants", "actor-1") in store.calls
    assert (
        "query_envelopes",
        {"thread_id": "thread-1", "sender_actor_id": "actor-1", "limit": 2},
    ) in store.calls
    assert (
        "query_deliveries",
        {"recipient_actor_id": "actor-1", "state": "PENDING", "limit": 100},
    ) in store.calls
    assert (
        "query_deliveries",
        {"recipient_actor_id": "actor-1", "state": "ACKED", "limit": 2},
    ) in store.calls
    assert (
        "query_work_items",
        {"owner_actor_id": "actor-1", "status": "RUNNING", "limit": 3},
    ) in store.calls
    assert (
        "query_sessions",
        {
            "actor_id": "actor-1",
            "surface": "cli",
            "external_key": "ext-1",
            "state": "OPEN",
            "limit": 4,
        },
    ) in store.calls
    assert (
        "query_attachments",
        {"actor_id": "actor-1", "kind": "TERMINAL", "limit": 5},
    ) in store.calls
    assert ("get_domain_events", {"since": since, "limit": 6}) in store.calls


async def test_v2_query_api_raises_not_found_errors() -> None:
    store = FakeQueryStore()
    store.actor = None
    store.thread = None
    store.envelope = None
    store.delivery = None
    store.work_item = None
    store.get_presence = lambda attachment_id: None  # type: ignore[method-assign]
    api = QueryAPI(store)

    with pytest.raises(ActorNotFoundError):
        await api.get_actor("missing")
    with pytest.raises(ThreadNotFoundError):
        await api.get_thread("missing")
    with pytest.raises(EnvelopeNotFoundError):
        await api.get_envelope("missing")
    with pytest.raises(DeliveryNotFoundError):
        await api.get_delivery("missing")
    with pytest.raises(WorkItemNotFoundError):
        await api.get_work_item("missing")


async def test_v2_query_api_get_presence_returns_none_when_absent() -> None:
    store = FakeQueryStore()

    async def missing_presence(attachment_id: str):
        store.calls.append(("get_presence", attachment_id))
        return None

    store.get_presence = missing_presence  # type: ignore[method-assign]
    api = QueryAPI(store)

    assert await api.get_presence("attachment-missing") is None
