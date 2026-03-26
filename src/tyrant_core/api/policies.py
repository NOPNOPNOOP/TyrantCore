"""v2 授权策略。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from tyrant_core.models import (
    ActorKind,
    Grant,
    GrantEffect,
    GrantScopeType,
    OrgEdgeKind,
    PolicyDeniedError,
)
from tyrant_core.store import Store


class PolicyError(Exception):
    """策略检查失败。"""


@dataclass(slots=True)
class PolicyDecision:
    """一次策略判定结果。"""

    allowed: bool
    reason: str
    matched_grant_ids: list[str]


class PolicyEngine:
    """基于 Grant 的最小授权引擎。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def can(
        self,
        action: str,
        subject_id: str,
        target_id: str | None = None,
    ) -> PolicyDecision:
        if not action:
            raise PolicyError("action is required")
        if not subject_id:
            raise PolicyError("subject_id is required")

        async with self.store.transaction():
            actor = await self.store.get_actor(subject_id)
            if actor is None:
                return PolicyDecision(
                    allowed=False,
                    reason=f"subject_not_found:{subject_id}",
                    matched_grant_ids=[],
                )
            if actor.kind == ActorKind.TYRANT:
                return PolicyDecision(
                    allowed=True,
                    reason="tyrant_implicit_global_access",
                    matched_grant_ids=[],
                )

            grants = await self.store.list_grants(subject_id)
            matched = [
                grant
                for grant in grants
                if self._action_matches(grant, action)
                and await self._scope_matches(grant, target_id)
            ]

        deny = [grant.id for grant in matched if grant.effect == GrantEffect.DENY]
        if deny:
            return PolicyDecision(
                allowed=False,
                reason="explicit_deny",
                matched_grant_ids=deny,
            )

        allow = [grant.id for grant in matched if grant.effect == GrantEffect.ALLOW]
        if allow:
            return PolicyDecision(
                allowed=True,
                reason="explicit_allow",
                matched_grant_ids=allow,
            )

        return PolicyDecision(
            allowed=False,
            reason="no_matching_grant",
            matched_grant_ids=[],
        )

    async def check(
        self,
        action: str,
        subject_id: str,
        target_id: str | None = None,
    ) -> None:
        decision = await self.can(action, subject_id, target_id=target_id)
        if not decision.allowed:
            raise PolicyDeniedError(
                f"{action} denied for {subject_id}: {decision.reason}"
            )

    @staticmethod
    def _action_matches(grant: Grant, action: str) -> bool:
        return grant.action in {"*", action}

    async def _scope_matches(self, grant: Grant, target_id: str | None) -> bool:
        scope = grant.scope
        if scope.type == GrantScopeType.GLOBAL:
            return True
        if target_id is None:
            return False
        if scope.type in {
            GrantScopeType.ACTORS,
            GrantScopeType.THREADS,
            GrantScopeType.WORK_ITEMS,
        }:
            return target_id in scope.targets
        if scope.type == GrantScopeType.SUBTREE:
            return await self._is_in_subtree(target_id, set(scope.targets))
        return False

    async def _is_in_subtree(self, target_id: str, roots: set[str]) -> bool:
        if target_id in roots:
            return True

        queue: deque[str] = deque(roots)
        visited = set(roots)
        while queue:
            current = queue.popleft()
            edges = await self.store.list_org_edges(
                kind=OrgEdgeKind.REPORTS_TO.value,
                src_actor_id=current,
            )
            for edge in edges:
                if edge.ended_at is not None:
                    continue
                child_id = edge.dst_actor_id
                if child_id == target_id:
                    return True
                if child_id in visited:
                    continue
                visited.add(child_id)
                queue.append(child_id)
        return False
