"""默认模板创建器。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from tyrant_core.models import (
    ActivationPolicy,
    Actor,
    ActorBlueprint,
    ActorControl,
    DesiredState,
    OrgEdge,
    OrgEdgeKind,
    ProvisionRequest,
    RuntimeKind,
)

from .base import ProvisionOutcome


class TemplateProvisioner:
    """最小可用创建器：按 blueprint 产出组织对象与 control。"""

    name = "template"

    async def supports(self, blueprint: ActorBlueprint) -> bool:
        return True

    async def provision(self, request: ProvisionRequest) -> ProvisionOutcome:
        now = datetime.now(timezone.utc)
        blueprint = request.blueprint
        actor_id = blueprint.metadata.get("actor_id", str(uuid4()))
        slug = blueprint.metadata.get("slug", blueprint.name.lower().replace(" ", "_"))
        actor_metadata = {
            key: value
            for key, value in blueprint.metadata.items()
            if key not in {"actor_id", "slug"}
        }
        if blueprint.bootstrap_spec:
            existing_bootstrap = actor_metadata.get("bootstrap", {})
            if not isinstance(existing_bootstrap, dict):
                existing_bootstrap = {}
            merged_bootstrap = dict(existing_bootstrap)
            merged_bootstrap.update(dict(blueprint.bootstrap_spec))
            actor_metadata["bootstrap"] = merged_bootstrap
        runtime_spec = dict(blueprint.runtime_spec)
        desired_state = DesiredState(
            runtime_spec.get("desired_state", DesiredState.ACTIVE.value)
        )
        activation_policy = ActivationPolicy(
            runtime_spec.get("activation_policy", ActivationPolicy.AUTO_START.value)
        )
        runtime_kind = RuntimeKind(
            runtime_spec.get("runtime_kind", RuntimeKind.LOCAL.value)
        )
        manager_actor_id = (
            blueprint.manager_actor_id
            or request.manager_actor_id
            or request.requested_by_actor_id
        )
        profile_id = blueprint.profile_id or request.profile_id

        actor = Actor(
            id=actor_id,
            kind=blueprint.kind,
            name=blueprint.name,
            slug=slug,
            org_role=blueprint.org_role,
            tags=list(blueprint.tags),
            metadata=actor_metadata,
            created_at=now,
        )
        org_edge = OrgEdge(
            id=str(uuid4()),
            kind=OrgEdgeKind.REPORTS_TO,
            src_actor_id=request.target_parent_actor_id,
            dst_actor_id=actor.id,
            created_at=now,
        )
        actor_control = ActorControl(
            id=str(uuid4()),
            actor_id=actor.id,
            manager_actor_id=manager_actor_id,
            profile_id=profile_id,
            desired_state=desired_state,
            activation_policy=activation_policy,
            runtime_kind=runtime_kind,
            placement=dict(runtime_spec.get("placement", {})),
            limits=dict(runtime_spec.get("limits", {})),
            updated_at=now,
        )
        return ProvisionOutcome(
            actor=actor,
            org_edge=org_edge,
            actor_control=actor_control,
        )
