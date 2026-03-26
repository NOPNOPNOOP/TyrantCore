"""创建平面基础协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from tyrant_core.models import (
    Actor,
    ActorControl,
    ActorProfile,
    Grant,
    OrgEdge,
    ProvisionRequest,
)


@dataclass(slots=True)
class ProvisionOutcome:
    actor: Actor
    org_edge: OrgEdge
    actor_control: ActorControl
    grants: list[Grant] = field(default_factory=list)
    actor_profile: ActorProfile | None = None


class ActorProvisioner(Protocol):
    name: str

    async def supports(self, blueprint) -> bool: ...

    async def provision(self, request: ProvisionRequest) -> ProvisionOutcome: ...


class ProvisioningQueueProcessorLike(Protocol):
    """创建工单队列处理器协议。"""

    async def process_next(
        self,
        *,
        owner_actor_id: str,
        executor_actor_id: str,
        auto_reconcile: bool = True,
        scan_limit: int = 20,
    ) -> dict | None: ...
