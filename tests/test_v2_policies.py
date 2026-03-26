from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tyrant_core.api.policies import PolicyEngine, PolicyError
from tyrant_core.models import (
    Actor,
    ActorKind,
    Grant,
    GrantEffect,
    GrantScope,
    GrantScopeType,
    OrgEdge,
    OrgEdgeKind,
    PolicyDeniedError,
)


def _actor(actor_id: str, kind: ActorKind = ActorKind.AGENT) -> Actor:
    return Actor(
        id=actor_id,
        kind=kind,
        name=actor_id,
        slug=actor_id,
        created_at=datetime.now(timezone.utc),
    )


def _grant(
    grant_id: str,
    *,
    action: str,
    effect: GrantEffect = GrantEffect.ALLOW,
    scope_type: GrantScopeType = GrantScopeType.GLOBAL,
    targets: list[str] | None = None,
) -> Grant:
    return Grant(
        id=grant_id,
        subject_actor_id="worker",
        action=action,
        effect=effect,
        scope=GrantScope(type=scope_type, targets=list(targets or [])),
        created_at=datetime.now(timezone.utc),
    )


def _edge(src_actor_id: str, dst_actor_id: str, *, ended: bool = False) -> OrgEdge:
    return OrgEdge(
        id=f"{src_actor_id}->{dst_actor_id}",
        kind=OrgEdgeKind.REPORTS_TO,
        src_actor_id=src_actor_id,
        dst_actor_id=dst_actor_id,
        created_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc) if ended else None,
    )


class FakePolicyStore:
    def __init__(
        self,
        *,
        actors: dict[str, Actor] | None = None,
        grants: dict[str, list[Grant]] | None = None,
        edges: dict[str, list[OrgEdge]] | None = None,
    ) -> None:
        self.actors = dict(actors or {})
        self.grants = {key: list(value) for key, value in (grants or {}).items()}
        self.edges = {key: list(value) for key, value in (edges or {}).items()}
        self.edge_queries: list[dict] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def get_actor(self, actor_id: str) -> Actor | None:
        return self.actors.get(actor_id)

    async def list_grants(self, subject_actor_id: str) -> list[Grant]:
        return list(self.grants.get(subject_actor_id, []))

    async def list_org_edges(self, **filters) -> list[OrgEdge]:
        self.edge_queries.append(dict(filters))
        return list(self.edges.get(filters["src_actor_id"], []))


async def test_v2_policy_engine_validates_input_and_handles_missing_or_tyrant() -> None:
    engine = PolicyEngine(FakePolicyStore(actors={"tyrant": _actor("tyrant", ActorKind.TYRANT)}))

    with pytest.raises(PolicyError):
        await engine.can("", "worker")
    with pytest.raises(PolicyError):
        await engine.can("envelope.send", "")

    missing = await engine.can("envelope.send", "missing")
    tyrant = await engine.can("envelope.send", "tyrant", target_id="anyone")

    assert missing.allowed is False
    assert missing.reason == "subject_not_found:missing"
    assert tyrant.allowed is True
    assert tyrant.reason == "tyrant_implicit_global_access"


async def test_v2_policy_engine_matches_actions_and_all_scope_types() -> None:
    store = FakePolicyStore(
        actors={"worker": _actor("worker")},
        grants={
            "worker": [
                _grant("g-global", action="*"),
                _grant("g-actor", action="actor.read", scope_type=GrantScopeType.ACTORS, targets=["actor-1"]),
                _grant("g-thread", action="thread.read", scope_type=GrantScopeType.THREADS, targets=["thread-1"]),
                _grant("g-work", action="work.read", scope_type=GrantScopeType.WORK_ITEMS, targets=["work-1"]),
            ]
        },
    )
    engine = PolicyEngine(store)

    assert PolicyEngine._action_matches(_grant("g", action="*"), "anything") is True
    assert PolicyEngine._action_matches(_grant("g", action="actor.read"), "actor.read") is True
    assert PolicyEngine._action_matches(_grant("g", action="actor.read"), "actor.write") is False

    global_decision = await engine.can("envelope.send", "worker")
    actor_decision = await engine.can("actor.read", "worker", target_id="actor-1")
    thread_decision = await engine.can("thread.read", "worker", target_id="thread-1")
    work_decision = await engine.can("work.read", "worker", target_id="work-1")
    missing_target = await engine.can("actor.read", "worker")

    assert global_decision.allowed is True
    assert global_decision.matched_grant_ids == ["g-global"]
    assert actor_decision.allowed is True
    assert actor_decision.matched_grant_ids == ["g-global", "g-actor"]
    assert thread_decision.allowed is True
    assert thread_decision.matched_grant_ids == ["g-global", "g-thread"]
    assert work_decision.allowed is True
    assert work_decision.matched_grant_ids == ["g-global", "g-work"]
    assert missing_target.allowed is True
    assert missing_target.matched_grant_ids == ["g-global"]


async def test_v2_policy_engine_subtree_deny_precedence_and_check() -> None:
    store = FakePolicyStore(
        actors={"worker": _actor("worker")},
        grants={
            "worker": [
                _grant("allow-subtree", action="envelope.send", scope_type=GrantScopeType.SUBTREE, targets=["lead"]),
                _grant(
                    "deny-worker",
                    action="envelope.send",
                    effect=GrantEffect.DENY,
                    scope_type=GrantScopeType.ACTORS,
                    targets=["worker-1"],
                ),
            ]
        },
        edges={
            "lead": [
                _edge("lead", "worker-1"),
                _edge("lead", "ended-child", ended=True),
            ]
        },
    )
    engine = PolicyEngine(store)

    root_allowed = await engine.can("envelope.send", "worker", target_id="lead")
    denied = await engine.can("envelope.send", "worker", target_id="worker-1")
    no_match = await engine.can("thread.read", "worker", target_id="worker-1")

    assert root_allowed.allowed is True
    assert root_allowed.reason == "explicit_allow"
    assert root_allowed.matched_grant_ids == ["allow-subtree"]
    assert denied.allowed is False
    assert denied.reason == "explicit_deny"
    assert denied.matched_grant_ids == ["deny-worker"]
    assert no_match.allowed is False
    assert no_match.reason == "no_matching_grant"

    with pytest.raises(PolicyDeniedError):
        await engine.check("envelope.send", "worker", target_id="worker-1")

    await engine.check("envelope.send", "worker", target_id="lead")


async def test_v2_policy_engine_handles_unknown_scope_and_subtree_cycles() -> None:
    store = FakePolicyStore(
        actors={"worker": _actor("worker")},
        edges={
            "root": [_edge("root", "mid")],
            "mid": [_edge("mid", "root")],
            "empty": [],
        },
    )
    engine = PolicyEngine(store)
    weird_grant = SimpleNamespace(scope=SimpleNamespace(type="UNKNOWN", targets=[]))

    assert await engine._scope_matches(weird_grant, "target") is False
    assert await engine._is_in_subtree("missing", {"root"}) is False
    assert await engine._is_in_subtree("missing", {"empty"}) is False
    assert store.edge_queries == [
        {"kind": "REPORTS_TO", "src_actor_id": "root"},
        {"kind": "REPORTS_TO", "src_actor_id": "mid"},
        {"kind": "REPORTS_TO", "src_actor_id": "empty"},
    ]


async def test_v2_policy_engine_skips_ended_edges_in_subtree_walk() -> None:
    store = FakePolicyStore(
        actors={"worker": _actor("worker")},
        edges={
            "lead": [_edge("lead", "worker-1", ended=True)],
        },
    )
    engine = PolicyEngine(store)

    assert await engine._is_in_subtree("worker-1", {"lead"}) is False
