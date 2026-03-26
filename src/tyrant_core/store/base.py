"""v2 Store 抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncContextManager, AsyncIterator

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


class Store(ABC):
    """v2 持久化层抽象基类。"""

    @abstractmethod
    async def save_actor(self, actor: Actor) -> None: ...

    @abstractmethod
    async def get_actor(self, actor_id: str) -> Actor | None: ...

    @abstractmethod
    async def update_actor(self, actor_id: str, **fields) -> None: ...

    @abstractmethod
    async def list_actors(self, **filters) -> list[Actor]: ...

    @abstractmethod
    async def save_org_edge(self, edge: OrgEdge) -> None: ...

    @abstractmethod
    async def list_org_edges(self, **filters) -> list[OrgEdge]: ...

    @abstractmethod
    async def save_grant(self, grant: Grant) -> None: ...

    @abstractmethod
    async def list_grants(self, subject_actor_id: str) -> list[Grant]: ...

    @abstractmethod
    async def save_actor_profile(self, profile: ActorProfile) -> None: ...

    @abstractmethod
    async def get_actor_profile(self, profile_id: str) -> ActorProfile | None: ...

    @abstractmethod
    async def query_actor_profiles(
        self,
        *,
        archived: bool | None = None,
        limit: int = 50,
    ) -> list[ActorProfile]: ...

    @abstractmethod
    async def save_actor_control(self, control: ActorControl) -> None: ...

    @abstractmethod
    async def get_actor_control(self, actor_id: str) -> ActorControl | None: ...

    @abstractmethod
    async def update_actor_control(self, actor_id: str, **fields) -> None: ...

    @abstractmethod
    async def query_actor_controls(
        self,
        *,
        manager_actor_id: str | None = None,
        desired_state: str | None = None,
        runtime_kind: str | None = None,
        limit: int = 50,
    ) -> list[ActorControl]: ...

    @abstractmethod
    async def save_thread(self, thread: Thread) -> None: ...

    @abstractmethod
    async def get_thread(self, thread_id: str) -> Thread | None: ...

    @abstractmethod
    async def save_envelope(self, envelope: Envelope) -> None: ...

    @abstractmethod
    async def get_envelope(self, envelope_id: str) -> Envelope | None: ...

    @abstractmethod
    async def query_envelopes(
        self,
        *,
        thread_id: str | None = None,
        sender_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[Envelope]: ...

    @abstractmethod
    async def save_delivery(self, delivery: Delivery) -> None: ...

    @abstractmethod
    async def get_delivery(self, delivery_id: str) -> Delivery | None: ...

    @abstractmethod
    async def lease_deliveries(
        self,
        *,
        recipient_actor_id: str,
        lease_owner: str,
        limit: int = 1,
        lease_seconds: int = 30,
    ) -> list[Delivery]: ...

    @abstractmethod
    async def ack_delivery(self, delivery_id: str) -> None: ...

    @abstractmethod
    async def fail_delivery(self, delivery_id: str, error: dict | None = None) -> None: ...

    @abstractmethod
    async def query_deliveries(
        self,
        *,
        recipient_actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Delivery]: ...

    @abstractmethod
    async def save_binding(self, binding: Binding) -> None: ...

    @abstractmethod
    async def query_bindings(
        self,
        *,
        surface: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[Binding]: ...

    @abstractmethod
    async def resolve_binding(
        self,
        *,
        surface: str,
        account_id: str | None = None,
        peer: str | None = None,
    ) -> Binding | None: ...

    @abstractmethod
    async def save_session(self, session: Session) -> None: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> Session | None: ...

    @abstractmethod
    async def query_sessions(
        self,
        *,
        actor_id: str | None = None,
        surface: str | None = None,
        external_key: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[Session]: ...

    @abstractmethod
    async def save_attachment(self, attachment: Attachment) -> None: ...

    @abstractmethod
    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Attachment]: ...

    @abstractmethod
    async def save_presence(self, presence: Presence) -> None: ...

    @abstractmethod
    async def get_presence(self, attachment_id: str) -> Presence | None: ...

    @abstractmethod
    async def save_work_item(self, work_item: WorkItem) -> None: ...

    @abstractmethod
    async def get_work_item(self, work_item_id: str) -> WorkItem | None: ...

    @abstractmethod
    async def update_work_item(self, work_item_id: str, **fields) -> None: ...

    @abstractmethod
    async def claim_work_item(
        self,
        work_item_id: str,
        *,
        owner_actor_id: str | None = None,
    ) -> bool: ...

    @abstractmethod
    async def query_work_items(
        self,
        *,
        owner_actor_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[WorkItem]: ...

    @abstractmethod
    async def save_provision_request(self, request: ProvisionRequest) -> None: ...

    @abstractmethod
    async def get_provision_request(
        self,
        request_id: str,
    ) -> ProvisionRequest | None: ...

    @abstractmethod
    async def claim_provision_request(
        self,
        request_id: str,
        executor_actor_id: str,
    ) -> bool: ...

    @abstractmethod
    async def update_provision_request(self, request_id: str, **fields) -> None: ...

    @abstractmethod
    async def query_provision_requests(
        self,
        *,
        status: str | None = None,
        requested_by_actor_id: str | None = None,
        target_parent_actor_id: str | None = None,
        limit: int = 50,
    ) -> list[ProvisionRequest]: ...

    @abstractmethod
    async def save_provision_record(self, record: ProvisionRecord) -> None: ...

    @abstractmethod
    async def get_provision_record(
        self,
        record_id: str,
    ) -> ProvisionRecord | None: ...

    @abstractmethod
    async def update_provision_record(self, record_id: str, **fields) -> None: ...

    @abstractmethod
    async def query_provision_records(
        self,
        *,
        request_id: str | None = None,
        produced_actor_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[ProvisionRecord]: ...

    @abstractmethod
    async def append_domain_event(self, event: DomainEvent) -> None: ...

    @abstractmethod
    async def get_domain_events(
        self,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DomainEvent]: ...

    @abstractmethod
    def transaction(self) -> AsyncContextManager:
        """async with store.transaction(): ..."""
        ...

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


@asynccontextmanager
async def noop_transaction() -> AsyncIterator[None]:
    """为临时内存实现提供的空事务。"""
    yield
