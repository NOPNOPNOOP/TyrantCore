"""本地 runtime 行为兼容导出层。"""

from .local_runtime import (
    BehaviorFactory,
    LocalRuntimeBehaviorInstaller,
    LocalRuntimeBehaviorRegistry,
    build_builtin_local_runtime_behavior_registry,
    build_echo_handler,
    build_noop_handler,
    build_template_reply_handler,
    install_builtin_local_runtime_behaviors,
    install_local_runtime_behavior_pack,
)

__all__ = [
    "BehaviorFactory",
    "LocalRuntimeBehaviorInstaller",
    "LocalRuntimeBehaviorRegistry",
    "build_builtin_local_runtime_behavior_registry",
    "build_echo_handler",
    "build_noop_handler",
    "build_template_reply_handler",
    "install_builtin_local_runtime_behaviors",
    "install_local_runtime_behavior_pack",
]
