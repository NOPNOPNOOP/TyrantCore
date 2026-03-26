"""v2 SQLite Store façade。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from tyrant_core.config import get_settings
from tyrant_core.models import (
    Actor,
    ActorControl,
    ActorProfile,
    Attachment,
    Binding,
    Delivery,
    DomainEvent,
    Envelope,
    Grant,
    OrgEdge,
    Presence,
    ProvisionRecord,
    ProvisionRequest,
    Session,
    Thread,
    WorkItem,
)
from tyrant_core.store.base import Store
from tyrant_core.store.sqlite.catalog_repo import CatalogRepository
from tyrant_core.store.sqlite.codec import (
    dt_to_str as _dt_to_str,
    model_to_row as _model_to_row,
    normalize_value as _normalize_value,
    row_to_model as _row_to_model,
)
from tyrant_core.store.sqlite.delivery_repo import DeliveryRepository
from tyrant_core.store.sqlite.management_repo import ManagementRepository
from tyrant_core.store.sqlite.provisioning_repo import ProvisioningRepository
from tyrant_core.store.sqlite.routing_repo import RoutingRepository
from tyrant_core.store.sqlite.tables import (
    actor_controls_table,
    actor_profiles_table,
    actors_table,
    attachments_table,
    bindings_table,
    deliveries_table,
    domain_events_table,
    envelopes_table,
    grants_table,
    metadata,
    org_edges_table,
    presences_table,
    provision_records_table,
    provision_requests_table,
    sessions_table,
    threads_table,
    work_items_table,
)
from tyrant_core.store.sqlite.thread_repo import ThreadRepository
from tyrant_core.store.sqlite.txn import SqliteSessionScope, build_default_database_url

__all__ = [
    "SqliteStore",
    "_default_database_url",
    "_dt_to_str",
    "_normalize_value",
    "_model_to_row",
    "_row_to_model",
    "metadata",
    "actors_table",
    "org_edges_table",
    "grants_table",
    "actor_profiles_table",
    "actor_controls_table",
    "threads_table",
    "envelopes_table",
    "deliveries_table",
    "bindings_table",
    "sessions_table",
    "attachments_table",
    "presences_table",
    "work_items_table",
    "provision_requests_table",
    "provision_records_table",
    "domain_events_table",
]


def _default_database_url() -> str:
    """兼容旧测试入口，允许通过模块级 monkeypatch 替换配置来源。"""
    return build_default_database_url(get_settings_fn=get_settings)


class SqliteStore(Store):
    """基于 SQLAlchemy async + aiosqlite 的 v2 Store。"""

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        database_url: str | None = None,
    ) -> None:
        self._scope = SqliteSessionScope(
            engine=engine,
            database_url=database_url,
            default_database_url=_default_database_url,
        )
        self._catalog = CatalogRepository(self._scope)
        self._threads = ThreadRepository(self._scope)
        self._routing = RoutingRepository(self._scope)
        self._delivery = DeliveryRepository(self._scope)
        self._management = ManagementRepository(self._scope)
        self._provisioning = ProvisioningRepository(self._scope)

    def transaction(self):
        return self._scope.transaction()

    async def initialize(self) -> None:
        await self._scope.initialize()

    async def close(self) -> None:
        await self._scope.close()

    async def save_actor(self, actor: Actor) -> None:
        await self._catalog.save_actor(actor)

    async def get_actor(self, actor_id: str) -> Actor | None:
        return await self._catalog.get_actor(actor_id)

    async def update_actor(self, actor_id: str, **fields) -> None:
        await self._catalog.update_actor(actor_id, **fields)

    async def list_actors(self, **filters) -> list[Actor]:
        return await self._catalog.list_actors(**filters)

    async def save_org_edge(self, edge: OrgEdge) -> None:
        await self._catalog.save_org_edge(edge)

    async def list_org_edges(self, **filters) -> list[OrgEdge]:
        return await self._catalog.list_org_edges(**filters)

    async def save_grant(self, grant: Grant) -> None:
        await self._catalog.save_grant(grant)

    async def list_grants(self, subject_actor_id: str) -> list[Grant]:
        return await self._catalog.list_grants(subject_actor_id)

    async def save_actor_profile(self, profile: ActorProfile) -> None:
        await self._management.save_actor_profile(profile)

    async def get_actor_profile(self, profile_id: str) -> ActorProfile | None:
        return await self._management.get_actor_profile(profile_id)

    async def query_actor_profiles(
        self,
        *,
        archived: bool | None = None,
        limit: int = 50,
    ) -> list[ActorProfile]:
        return await self._management.query_actor_profiles(
            archived=archived,
            limit=limit,
        )

    async def save_actor_control(self, control: ActorControl) -> None:
        await self._management.save_actor_control(control)

    async def get_actor_control(self, actor_id: str) -> ActorControl | None:
        return await self._management.get_actor_control(actor_id)

    async def update_actor_control(self, actor_id: str, **fields) -> None:
        await self._management.update_actor_control(actor_id, **fields)

    async def query_actor_controls(
        self,
        *,
        manager_actor_id: str | None = None,
        desired_state: str | None = None,
        runtime_kind: str | None = None,
        limit: int = 50,
    ) -> list[ActorControl]:
        return await self._management.query_actor_controls(
            manager_actor_id=manager_actor_id,
            desired_state=desired_state,
            runtime_kind=runtime_kind,
            limit=limit,
        )

    async def save_thread(self, thread: Thread) -> None:
        await self._threads.save_thread(thread)

    async def get_thread(self, thread_id: str) -> Thread | None:
        return await self._threads.get_thread(thread_id)

    async def save_envelope(self, envelope: Envelope) -> None:
        await self._threads.save_envelope(envelope)

    async def get_envelope(self, envelope_id: str) -> Envelope | None:
        return await self._threads.get_envelope(envelope_id)

    async def query_envelopes(
        self,
        *,
        thread_id: str | None = None,
        sender_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[Envelope]:
        return await self._threads.query_envelopes(
            thread_id=thread_id,
            sender_actor_id=sender_actor_id,
            limit=limit,
        )

    async def save_delivery(self, delivery: Delivery) -> None:
        await self._delivery.save_delivery(delivery)

    async def get_delivery(self, delivery_id: str) -> Delivery | None:
        return await self._delivery.get_delivery(delivery_id)

    async def lease_deliveries(
        self,
        *,
        recipient_actor_id: str,
        lease_owner: str,
        limit: int = 1,
        lease_seconds: int = 30,
    ) -> list[Delivery]:
        return await self._delivery.lease_deliveries(
            recipient_actor_id=recipient_actor_id,
            lease_owner=lease_owner,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def ack_delivery(self, delivery_id: str) -> None:
        await self._delivery.ack_delivery(delivery_id)

    async def fail_delivery(self, delivery_id: str, error: dict | None = None) -> None:
        await self._delivery.fail_delivery(delivery_id, error=error)

    async def query_deliveries(
        self,
        *,
        recipient_actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Delivery]:
        return await self._delivery.query_deliveries(
            recipient_actor_id=recipient_actor_id,
            state=state,
            limit=limit,
        )

    async def save_binding(self, binding: Binding) -> None:
        await self._routing.save_binding(binding)

    async def query_bindings(
        self,
        *,
        surface: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[Binding]:
        return await self._routing.query_bindings(
            surface=surface,
            actor_id=actor_id,
            limit=limit,
        )

    async def resolve_binding(
        self,
        *,
        surface: str,
        account_id: str | None = None,
        peer: str | None = None,
    ) -> Binding | None:
        return await self._routing.resolve_binding(
            surface=surface,
            account_id=account_id,
            peer=peer,
        )

    async def save_session(self, session: Session) -> None:
        await self._routing.save_session(session)

    async def get_session(self, session_id: str) -> Session | None:
        return await self._routing.get_session(session_id)

    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Session]:
        return await self._routing.query_sessions(
            actor_id=actor_id,
            surface=surface,
            external_key=external_key,
            state=state,
            limit=limit,
        )

    async def save_attachment(self, attachment: Attachment) -> None:
        await self._routing.save_attachment(attachment)

    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Attachment]:
        return await self._routing.query_attachments(
            actor_id=actor_id,
            kind=kind,
            limit=limit,
        )

    async def save_presence(self, presence: Presence) -> None:
        await self._routing.save_presence(presence)

    async def get_presence(self, attachment_id: str) -> Presence | None:
        return await self._routing.get_presence(attachment_id)

    async def save_work_item(self, work_item: WorkItem) -> None:
        await self._delivery.save_work_item(work_item)

    async def get_work_item(self, work_item_id: str) -> WorkItem | None:
        return await self._delivery.get_work_item(work_item_id)

    async def update_work_item(self, work_item_id: str, **fields) -> None:
        await self._delivery.update_work_item(work_item_id, **fields)

    async def claim_work_item(
        self,
        work_item_id: str,
        *,
        owner_actor_id: str | None = None,
    ) -> bool:
        return await self._delivery.claim_work_item(
            work_item_id,
            owner_actor_id=owner_actor_id,
        )

    async def query_work_items(
        self,
        *,
        owner_actor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[WorkItem]:
        return await self._delivery.query_work_items(
            owner_actor_id=owner_actor_id,
            status=status,
            limit=limit,
        )

    async def save_provision_request(self, request: ProvisionRequest) -> None:
        await self._provisioning.save_provision_request(request)

    async def get_provision_request(
        self,
        request_id: str,
    ) -> ProvisionRequest | None:
        return await self._provisioning.get_provision_request(request_id)

    async def claim_provision_request(
        self,
        request_id: str,
        executor_actor_id: str,
    ) -> bool:
        return await self._provisioning.claim_provision_request(
            request_id,
            executor_actor_id,
        )

    async def update_provision_request(self, request_id: str, **fields) -> None:
        await self._provisioning.update_provision_request(request_id, **fields)

    async def query_provision_requests(
        self,
        *,
        status: str | None = None,
        requested_by_actor_id: str | None = None,
        target_parent_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[ProvisionRequest]:
        return await self._provisioning.query_provision_requests(
            status=status,
            requested_by_actor_id=requested_by_actor_id,
            target_parent_actor_id=target_parent_actor_id,
            limit=limit,
        )

    async def save_provision_record(self, record: ProvisionRecord) -> None:
        await self._provisioning.save_provision_record(record)

    async def get_provision_record(self, record_id: str) -> ProvisionRecord | None:
        return await self._provisioning.get_provision_record(record_id)

    async def update_provision_record(self, record_id: str, **fields) -> None:
        await self._provisioning.update_provision_record(record_id, **fields)

    async def query_provision_records(
        self,
        *,
        request_id: str | None = None,
        produced_actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[ProvisionRecord]:
        return await self._provisioning.query_provision_records(
            request_id=request_id,
            produced_actor_id=produced_actor_id,
            state=state,
            limit=limit,
        )

    async def append_domain_event(self, event: DomainEvent) -> None:
        await self._delivery.append_domain_event(event)

    async def get_domain_events(
        self,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DomainEvent]:
        return await self._delivery.get_domain_events(
            since=since,
            limit=limit,
        )
