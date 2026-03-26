"""本地 runtime 行为安装器。"""

from __future__ import annotations

from .builtin_behaviors import (
    build_echo_handler,
    build_noop_handler,
    build_template_reply_handler,
)
from .registry import LocalRuntimeBehaviorInstaller, LocalRuntimeBehaviorRegistry


def install_builtin_local_runtime_behaviors(
    registry: LocalRuntimeBehaviorRegistry,
) -> LocalRuntimeBehaviorRegistry:
    """安装内置行为包。"""
    registry.register("echo", build_echo_handler)
    registry.register("template_reply", build_template_reply_handler)
    registry.register("noop", build_noop_handler)
    return registry


def build_builtin_local_runtime_behavior_registry() -> LocalRuntimeBehaviorRegistry:
    """构造一个已安装内置行为包的注册表。"""
    registry = LocalRuntimeBehaviorRegistry()
    return install_builtin_local_runtime_behaviors(registry)


def install_local_runtime_behavior_pack(
    registry: LocalRuntimeBehaviorRegistry,
    installer: LocalRuntimeBehaviorInstaller,
) -> LocalRuntimeBehaviorRegistry:
    """安装自定义行为包。"""
    registry.install(installer)
    return registry
