"""创建平面服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from tyrant_core.models import (
    ActorBlueprint,
    ActorNotFoundError,
    ActorProfileNotFoundError,
    ProvisionRecord,
    ProvisionRequest,
    ProvisionRequestNotFoundError,
    ProvisionStatus,
    ValidationError,
)
from tyrant_core.store import SqliteStore, Store

from .base import ProvisionOutcome
from .registry import ProvisionerRegistry
from .template_provisioner import TemplateProvisioner


class ProvisioningService:
    """负责提交与执行 ProvisionRequest。"""

    def __init__(
        self,
        store: Store | None = None,
        *,
        registry: ProvisionerRegistry | None = None,
    ) -> None:
        self.store = store or SqliteStore()
        self.registry = registry or ProvisionerRegistry([TemplateProvisioner()])

    async def submit_request(self, spec: dict) -> ProvisionRequest:
        now = datetime.now(timezone.utc)
        blueprint_spec = spec.get("blueprint", spec)
        blueprint = (
            blueprint_spec
            if isinstance(blueprint_spec, ActorBlueprint)
            else ActorBlueprint.model_validate(blueprint_spec)
        )
        request = ProvisionRequest(
            id=spec.get("id", str(uuid4())),
            requested_by_actor_id=spec["requested_by_actor_id"],
            target_parent_actor_id=spec.get(
                "target_parent_actor_id",
                blueprint.parent_actor_id,
            ),
            manager_actor_id=spec.get("manager_actor_id", blueprint.manager_actor_id),
            profile_id=spec.get("profile_id", blueprint.profile_id),
            provisioner_hint=spec.get("provisioner_hint"),
            priority=spec.get("priority", 0),
            status=ProvisionStatus(spec.get("status", ProvisionStatus.PENDING.value)),
            idempotency_key=spec.get("idempotency_key"),
            reason=spec.get("reason"),
            blueprint=blueprint,
            work_item_id=spec.get("work_item_id"),
            claimed_by_actor_id=spec.get("claimed_by_actor_id"),
            claimed_at=spec.get("claimed_at"),
            created_at=spec.get("created_at", now),
            closed_at=spec.get("closed_at"),
        )
        async with self.store.transaction():
            await self.store.save_provision_request(request)
        return request

    async def claim_request(self, request_id: str, executor_actor_id: str) -> bool:
        async with self.store.transaction():
            return await self.store.claim_provision_request(request_id, executor_actor_id)

    async def get_request(self, request_id: str) -> ProvisionRequest | None:
        async with self.store.transaction():
            return await self.store.get_provision_request(request_id)

    async def query_requests(self, **filters) -> list[ProvisionRequest]:
        async with self.store.transaction():
            return await self.store.query_provision_requests(**filters)

    async def query_records(self, **filters) -> list[ProvisionRecord]:
        async with self.store.transaction():
            return await self.store.query_provision_records(**filters)

    async def get_record(self, record_id: str) -> ProvisionRecord | None:
        async with self.store.transaction():
            return await self.store.get_provision_record(record_id)

    async def execute_request(
        self,
        request_id: str,
        *,
        executor_actor_id: str | None = None,
    ) -> ProvisionRecord:
        request = await self.get_request(request_id)
        if request is None:
            raise ProvisionRequestNotFoundError(request_id)
        if request.status in {
            ProvisionStatus.SUCCEEDED,
            ProvisionStatus.CANCELED,
        }:
            raise ValidationError(f"request is not executable: {request.status.value}")

        claim_owner = executor_actor_id or "system"
        if request.status == ProvisionStatus.PENDING:
            claimed = await self.claim_request(request.id, claim_owner)
            if not claimed:
                raise ValidationError(f"request already claimed: {request.id}")
            request = await self.get_request(request.id)
            assert request is not None

        await self._validate_request(request)
        provisioner = await self.registry.resolve(
            request.blueprint,
            hint=request.provisioner_hint,
        )
        record_id = str(uuid4())
        now = datetime.now(timezone.utc)
        try:
            outcome = await provisioner.provision(request)
            async with self.store.transaction():
                await self._persist_success(
                    request=request,
                    record_id=record_id,
                    outcome=outcome,
                    provisioner_name=provisioner.name,
                    executor_actor_id=claim_owner,
                    finished_at=now,
                )
                record = await self.store.get_provision_record(record_id)
            assert record is not None
            return record
        except Exception as exc:
            async with self.store.transaction():
                await self.store.save_provision_record(
                    ProvisionRecord(
                        id=record_id,
                        request_id=request.id,
                        provisioner_name=provisioner.name,
                        executor_actor_id=claim_owner,
                        state=ProvisionStatus.FAILED,
                        attempt_count=1,
                        last_error={"error": str(exc)},
                        created_at=now,
                        finished_at=now,
                    )
                )
                await self.store.update_provision_request(
                    request.id,
                    status=ProvisionStatus.FAILED,
                    closed_at=now,
                )
            raise

    async def _validate_request(self, request: ProvisionRequest) -> None:
        async with self.store.transaction():
            parent = await self.store.get_actor(request.target_parent_actor_id)
            if parent is None:
                raise ActorNotFoundError(request.target_parent_actor_id)

            manager_actor_id = (
                request.manager_actor_id
                or request.blueprint.manager_actor_id
                or request.requested_by_actor_id
            )
            manager = await self.store.get_actor(manager_actor_id)
            if manager is None:
                raise ActorNotFoundError(manager_actor_id)

            profile_id = request.profile_id or request.blueprint.profile_id
            if profile_id is not None:
                profile = await self.store.get_actor_profile(profile_id)
                if profile is None:
                    raise ActorProfileNotFoundError(profile_id)

    async def _persist_success(
        self,
        *,
        request: ProvisionRequest,
        record_id: str,
        outcome: ProvisionOutcome,
        provisioner_name: str,
        executor_actor_id: str,
        finished_at,
    ) -> None:
        await self.store.save_actor(outcome.actor)
        await self.store.save_org_edge(outcome.org_edge)
        for grant in outcome.grants:
            await self.store.save_grant(grant)
        if outcome.actor_profile is not None:
            await self.store.save_actor_profile(outcome.actor_profile)
        await self.store.save_actor_control(outcome.actor_control)
        await self.store.save_provision_record(
            ProvisionRecord(
                id=record_id,
                request_id=request.id,
                provisioner_name=provisioner_name,
                executor_actor_id=executor_actor_id,
                produced_actor_id=outcome.actor.id,
                org_edge_id=outcome.org_edge.id,
                control_id=outcome.actor_control.id,
                state=ProvisionStatus.SUCCEEDED,
                attempt_count=1,
                created_at=finished_at,
                finished_at=finished_at,
            )
        )
        await self.store.update_provision_request(
            request.id,
            status=ProvisionStatus.SUCCEEDED,
            closed_at=finished_at,
        )
