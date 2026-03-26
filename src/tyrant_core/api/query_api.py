"""v2 查询侧 API。"""

from __future__ import annotations

from typing import Any

from tyrant_core.models import (
    ActorNotFoundError,
    DeliveryNotFoundError,
    EnvelopeNotFoundError,
    ThreadNotFoundError,
    WorkItemNotFoundError,
)
from tyrant_core.store import SqliteStore, Store


class QueryAPI:
    """v2 查询侧边界。"""

    def __init__(self, store: Store | None = None) -> None:
        self.store = store or SqliteStore()

    async def get_actor(self, actor_id: str) -> dict[str, Any]:
        async with self.store.transaction():
            actor = await self.store.get_actor(actor_id)
        if actor is None:
            raise ActorNotFoundError(actor_id)
        return actor.model_dump(mode="json")

    async def list_actors(self, **filters) -> list[dict[str, Any]]:
        async with self.store.transaction():
            actors = await self.store.list_actors(**filters)
        return [actor.model_dump(mode="json") for actor in actors]

    async def list_org_edges(self, **filters) -> list[dict[str, Any]]:
        async with self.store.transaction():
            edges = await self.store.list_org_edges(**filters)
        return [edge.model_dump(mode="json") for edge in edges]

    async def list_grants(self, subject_actor_id: str) -> list[dict[str, Any]]:
        async with self.store.transaction():
            grants = await self.store.list_grants(subject_actor_id)
        return [grant.model_dump(mode="json") for grant in grants]

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        async with self.store.transaction():
            thread = await self.store.get_thread(thread_id)
        if thread is None:
            raise ThreadNotFoundError(thread_id)
        return thread.model_dump(mode="json")

    async def get_envelope(self, envelope_id: str) -> dict[str, Any]:
        async with self.store.transaction():
            envelope = await self.store.get_envelope(envelope_id)
        if envelope is None:
            raise EnvelopeNotFoundError(envelope_id)
        return envelope.model_dump(mode="json")

    async def query_envelopes(
        self,
        *,
        thread_id: str | None = None,
        sender_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self.store.transaction():
            envelopes = await self.store.query_envelopes(
                thread_id=thread_id,
                sender_actor_id=sender_actor_id,
                limit=limit,
            )
        return [envelope.model_dump(mode="json") for envelope in envelopes]

    async def get_delivery(self, delivery_id: str) -> dict[str, Any]:
        async with self.store.transaction():
            delivery = await self.store.get_delivery(delivery_id)
        if delivery is None:
            raise DeliveryNotFoundError(delivery_id)
        return delivery.model_dump(mode="json")

    async def list_pending_deliveries(self, actor_id: str) -> list[dict[str, Any]]:
        async with self.store.transaction():
            deliveries = await self.store.query_deliveries(
                recipient_actor_id=actor_id,
                state="PENDING",
                limit=100,
            )
        return [delivery.model_dump(mode="json") for delivery in deliveries]

    async def query_deliveries(
        self,
        *,
        actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self.store.transaction():
            deliveries = await self.store.query_deliveries(
                recipient_actor_id=actor_id,
                state=state,
                limit=limit,
            )
        return [delivery.model_dump(mode="json") for delivery in deliveries]

    async def get_work_item(self, work_item_id: str) -> dict[str, Any]:
        async with self.store.transaction():
            work_item = await self.store.get_work_item(work_item_id)
        if work_item is None:
            raise WorkItemNotFoundError(work_item_id)
        return work_item.model_dump(mode="json")

    async def query_work_items(
        self,
        *,
        owner_actor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self.store.transaction():
            items = await self.store.query_work_items(
                owner_actor_id=owner_actor_id,
                status=status,
                limit=limit,
            )
        return [item.model_dump(mode="json") for item in items]

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self.store.transaction():
            sessions = await self.store.query_sessions(
                actor_id=actor_id,
                surface=surface,
                external_key=external_key,
                state=state,
                limit=limit,
            )
        return [session.model_dump(mode="json") for session in sessions]

    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self.store.transaction():
            attachments = await self.store.query_attachments(
                actor_id=actor_id,
                kind=kind,
                limit=limit,
            )
        return [attachment.model_dump(mode="json") for attachment in attachments]

    async def get_presence(self, attachment_id: str) -> dict[str, Any] | None:
        async with self.store.transaction():
            presence = await self.store.get_presence(attachment_id)
        if presence is None:
            return None
        return presence.model_dump(mode="json")

    async def get_domain_events(
        self,
        *,
        since=None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with self.store.transaction():
            events = await self.store.get_domain_events(since=since, limit=limit)
        return [event.model_dump(mode="json") for event in events]
