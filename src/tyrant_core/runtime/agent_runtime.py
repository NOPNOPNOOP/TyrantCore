"""v2 过渡期 AgentRuntime。

v2 的正式概念是 ActorRuntime。这里保留 AgentRuntime 名字作为轻量包装，
避免默认命名空间仍然回到 v1 的 ADK + inbox queue 逻辑。
"""

from __future__ import annotations

from tyrant_core.runtime.actor_runtime import ActorRuntime


class AgentRuntime(ActorRuntime):
    """过渡期别名，语义上等同于 ActorRuntime。"""
