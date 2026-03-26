"""v2 命令侧 API。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tyrant_core.bus.event_bus import EventBus
from tyrant_core.api.policies import PolicyEngine, PolicyError
from tyrant_core.models import (
    Actor,
    ActorKind,
    Attachment,
    AttachmentDesiredState,
    AttachmentKind,
    Delivery,
    DomainEvent,
    Envelope,
    Grant,
    GrantEffect,
    GrantScope,
    GrantSourceType,
    OrgEdge,
    OrgEdgeKind,
    Presence,
    PresenceStatus,
    Priority,
    Thread,
    ThreadKind,
    WorkItem,
    WorkItemStatus,
)
from tyrant_core.store import SqliteStore, Store


@dataclass(slots=True)
class CommandResult:
    """统一命令返回。"""

    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)


class CommandAPI:
    """v2 命令侧边界。"""

    def __init__(
        self,
        store: Store | None = None,
        event_bus: EventBus | None = None,
        policies: PolicyEngine | None = None,
    ) -> None:
        self.store = store or SqliteStore()
        self.event_bus = event_bus or EventBus()
        self.policies = policies or PolicyEngine(self.store)

    async def _check_policy(
        self,
        *,
        action: str,
        caller_actor_id: str | None,
        bypass_policy: bool = False,
        target_id: str | None = None,
    ) -> None:
        if bypass_policy:
            return
        if caller_actor_id is None:
            raise PolicyError(f"{action} requires caller_actor_id or bypass_policy=True")
        await self.policies.check(action, caller_actor_id, target_id=target_id)

    async def create_actor(self, spec: dict[str, Any]) -> CommandResult:
        await self._check_policy(
            action="actor.create",
            caller_actor_id=spec.get("caller_actor_id"),
            bypass_policy=spec.get("bypass_policy", False),
        )
        now = datetime.now(timezone.utc)
        actor = Actor(
            id=spec.get("id", str(uuid4())),
            kind=ActorKind(spec.get("kind", ActorKind.AGENT.value)),
            name=spec["name"],
            slug=spec.get("slug", spec["name"].lower().replace(" ", "_")),
            org_role=spec.get("org_role"),
            tags=list(spec.get("tags", [])),
            metadata=dict(spec.get("metadata", {})),
            created_at=spec.get("created_at", now),
            archived_at=spec.get("archived_at"),
        )
        event = DomainEvent(
            id=str(uuid4()),
            type="actor.created",
            source=actor.id,
            payload={"actor_id": actor.id, "kind": actor.kind.value},
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_actor(actor)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={"actor": actor.model_dump(mode="json")})

    async def create_org_edge(self, spec: dict[str, Any]) -> CommandResult:
        await self._check_policy(
            action="org_edge.create",
            caller_actor_id=spec.get("caller_actor_id"),
            bypass_policy=spec.get("bypass_policy", False),
            target_id=spec.get("dst_actor_id"),
        )
        now = datetime.now(timezone.utc)
        edge = OrgEdge(
            id=spec.get("id", str(uuid4())),
            kind=OrgEdgeKind(spec.get("kind", OrgEdgeKind.REPORTS_TO.value)),
            src_actor_id=spec["src_actor_id"],
            dst_actor_id=spec["dst_actor_id"],
            metadata=dict(spec.get("metadata", {})),
            created_at=spec.get("created_at", now),
            ended_at=spec.get("ended_at"),
        )
        event = DomainEvent(
            id=str(uuid4()),
            type="org_edge.created",
            source=edge.src_actor_id,
            payload={
                "edge_id": edge.id,
                "kind": edge.kind.value,
                "src_actor_id": edge.src_actor_id,
                "dst_actor_id": edge.dst_actor_id,
            },
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_org_edge(edge)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={"org_edge": edge.model_dump(mode="json")})

    async def issue_grant(self, spec: dict[str, Any]) -> CommandResult:
        await self._check_policy(
            action="grant.issue",
            caller_actor_id=spec.get("caller_actor_id"),
            bypass_policy=spec.get("bypass_policy", False),
            target_id=spec["subject_actor_id"],
        )
        now = datetime.now(timezone.utc)
        grant = Grant(
            id=spec.get("id", str(uuid4())),
            subject_actor_id=spec["subject_actor_id"],
            action=spec["action"],
            effect=GrantEffect(spec.get("effect", GrantEffect.ALLOW.value)),
            scope=GrantScope.model_validate(spec.get("scope", {})),
            source_type=GrantSourceType(spec.get("source_type", GrantSourceType.DIRECT.value)),
            source_actor_id=spec.get("source_actor_id"),
            delegable=spec.get("delegable", False),
            expires_at=spec.get("expires_at"),
            created_at=spec.get("created_at", now),
            revoked_at=spec.get("revoked_at"),
        )
        event = DomainEvent(
            id=str(uuid4()),
            type="grant.issued",
            source=spec.get("caller_actor_id") or grant.source_actor_id or grant.subject_actor_id,
            payload={
                "grant_id": grant.id,
                "subject_actor_id": grant.subject_actor_id,
                "action": grant.action,
                "effect": grant.effect.value,
            },
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_grant(grant)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={"grant": grant.model_dump(mode="json")})

    async def open_thread(self, spec: dict[str, Any]) -> CommandResult:
        await self._check_policy(
            action="thread.open",
            caller_actor_id=spec.get("caller_actor_id"),
            bypass_policy=spec.get("bypass_policy", False),
        )
        now = datetime.now(timezone.utc)
        thread = Thread(
            id=spec.get("id", str(uuid4())),
            kind=ThreadKind(spec.get("kind", ThreadKind.DIALOG.value)),
            subject=spec.get("subject", ""),
            created_by=spec["created_by"],
            participants=list(spec.get("participants", [])),
            metadata=dict(spec.get("metadata", {})),
            created_at=spec.get("created_at", now),
            closed_at=spec.get("closed_at"),
        )
        event = DomainEvent(
            id=str(uuid4()),
            type="thread.opened",
            source=thread.created_by,
            payload={"thread_id": thread.id},
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_thread(thread)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={"thread": thread.model_dump(mode="json")})

    async def send_envelope(self, spec: dict[str, Any]) -> CommandResult:
        caller_actor_id = spec.get("caller_actor_id")
        for recipient_actor_id in list(
            spec.get("recipient_actor_ids")
            or ([spec["recipient_actor_id"]] if spec.get("recipient_actor_id") else [])
        ):
            await self._check_policy(
                action="envelope.send",
                caller_actor_id=caller_actor_id,
                bypass_policy=spec.get("bypass_policy", False),
                target_id=recipient_actor_id,
            )
        now = datetime.now(timezone.utc)
        recipient_ids = list(
            spec.get("recipient_actor_ids")
            or ([spec["recipient_actor_id"]] if spec.get("recipient_actor_id") else [])
        )
        thread_id = spec.get("thread_id")
        if thread_id is None:
            thread_result = await self.open_thread({
                "created_by": spec["sender_actor_id"],
                "participants": [spec["sender_actor_id"], *recipient_ids],
                "subject": spec.get("subject", ""),
                "kind": spec.get("thread_kind", ThreadKind.DIALOG.value),
                "metadata": spec.get("thread_metadata", {}),
                "bypass_policy": True,
            })
            thread_id = thread_result.payload["thread"]["id"]

        envelope = Envelope(
            id=spec.get("id", str(uuid4())),
            thread_id=thread_id,
            sender_actor_id=spec["sender_actor_id"],
            kind=spec.get("kind", "CHAT"),
            payload=dict(spec.get("payload", {})),
            priority=Priority(spec.get("priority", Priority.NORMAL.value)),
            correlation_id=spec.get("correlation_id"),
            idempotency_key=spec.get("idempotency_key"),
            references=list(spec.get("references", [])),
            created_at=spec.get("created_at", now),
            expires_at=spec.get("expires_at"),
        )
        deliveries: list[Delivery] = []
        event = DomainEvent(
            id=str(uuid4()),
            type="envelope.sent",
            source=envelope.sender_actor_id,
            payload={
                "envelope_id": envelope.id,
                "recipient_actor_ids": recipient_ids,
                "thread_id": thread_id,
            },
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_envelope(envelope)
            for recipient_actor_id in recipient_ids:
                delivery = Delivery(
                    id=str(uuid4()),
                    envelope_id=envelope.id,
                    recipient_actor_id=recipient_actor_id,
                    target_attachment_id=spec.get("target_attachment_id"),
                    created_at=now,
                )
                await self.store.save_delivery(delivery)
                deliveries.append(delivery)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={
            "thread_id": thread_id,
            "envelope": envelope.model_dump(mode="json"),
            "deliveries": [delivery.model_dump(mode="json") for delivery in deliveries],
        })

    async def register_attachment(self, spec: dict[str, Any]) -> CommandResult:
        await self._check_policy(
            action="attachment.register",
            caller_actor_id=spec.get("caller_actor_id"),
            bypass_policy=spec.get("bypass_policy", False),
            target_id=spec["actor_id"],
        )
        now = datetime.now(timezone.utc)
        attachment = Attachment(
            id=spec.get("id", str(uuid4())),
            actor_id=spec["actor_id"],
            kind=AttachmentKind(spec.get("kind", AttachmentKind.LOCAL_RUNTIME.value)),
            endpoint=dict(spec.get("endpoint", {})),
            capabilities=list(spec.get("capabilities", [])),
            desired_state=AttachmentDesiredState(
                spec.get("desired_state", AttachmentDesiredState.ACTIVE.value)
            ),
            created_at=spec.get("created_at", now),
            detached_at=spec.get("detached_at"),
        )
        event = DomainEvent(
            id=str(uuid4()),
            type="attachment.registered",
            source=attachment.actor_id,
            payload={
                "attachment_id": attachment.id,
                "actor_id": attachment.actor_id,
                "kind": attachment.kind.value,
            },
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_attachment(attachment)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={"attachment": attachment.model_dump(mode="json")})

    async def report_presence(self, spec: dict[str, Any]) -> CommandResult:
        await self._check_policy(
            action="presence.report",
            caller_actor_id=spec.get("caller_actor_id"),
            bypass_policy=spec.get("bypass_policy", False),
            target_id=spec["actor_id"],
        )
        now = datetime.now(timezone.utc)
        presence = Presence(
            attachment_id=spec["attachment_id"],
            actor_id=spec["actor_id"],
            gateway_id=spec["gateway_id"],
            status=PresenceStatus(spec.get("status", PresenceStatus.ONLINE.value)),
            leases=spec.get("leases", 0),
            last_heartbeat_at=spec.get("last_heartbeat_at", now),
        )
        event = DomainEvent(
            id=str(uuid4()),
            type="presence.reported",
            source=presence.actor_id,
            payload={
                "attachment_id": presence.attachment_id,
                "actor_id": presence.actor_id,
                "status": presence.status.value,
                "gateway_id": presence.gateway_id,
            },
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_presence(presence)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={"presence": presence.model_dump(mode="json")})

    async def create_work_item(self, spec: dict[str, Any]) -> CommandResult:
        await self._check_policy(
            action="work_item.create",
            caller_actor_id=spec.get("caller_actor_id"),
            bypass_policy=spec.get("bypass_policy", False),
            target_id=spec.get("owner_actor_id"),
        )
        now = datetime.now(timezone.utc)
        thread_id = spec.get("thread_id")
        if thread_id is None:
            thread_result = await self.open_thread({
                "created_by": spec["creator_actor_id"],
                "participants": [spec["creator_actor_id"], spec["owner_actor_id"]],
                "subject": spec.get("subject", spec.get("kind", "TASK")),
                "kind": ThreadKind.TASK.value,
                "bypass_policy": True,
            })
            thread_id = thread_result.payload["thread"]["id"]
        work_item = WorkItem(
            id=spec.get("id", str(uuid4())),
            kind=spec.get("kind", "TASK"),
            creator_actor_id=spec["creator_actor_id"],
            owner_actor_id=spec["owner_actor_id"],
            thread_id=thread_id,
            parent_work_item_id=spec.get("parent_work_item_id"),
            status=WorkItemStatus(spec.get("status", WorkItemStatus.PENDING.value)),
            spec=dict(spec.get("spec", {})),
            result=dict(spec.get("result", {})),
            created_at=spec.get("created_at", now),
            updated_at=spec.get("updated_at", now),
            closed_at=spec.get("closed_at"),
        )
        event = DomainEvent(
            id=str(uuid4()),
            type="work_item.created",
            source=work_item.creator_actor_id,
            payload={"work_item_id": work_item.id, "thread_id": work_item.thread_id},
            created_at=now,
        )
        async with self.store.transaction():
            await self.store.save_work_item(work_item)
            await self.store.append_domain_event(event)
        await self.event_bus.publish(event)
        return CommandResult(payload={"work_item": work_item.model_dump(mode="json")})
