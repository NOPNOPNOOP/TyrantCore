"""本地 runtime 行为包。"""

from .builtin_behaviors import (
    build_echo_handler,
    build_noop_handler,
    build_template_reply_handler,
)
from .installers import (
    build_builtin_local_runtime_behavior_registry,
    install_builtin_local_runtime_behaviors,
    install_local_runtime_behavior_pack,
)
from .registry import (
    BehaviorFactory,
    LocalRuntimeBehaviorInstaller,
    LocalRuntimeBehaviorRegistry,
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
