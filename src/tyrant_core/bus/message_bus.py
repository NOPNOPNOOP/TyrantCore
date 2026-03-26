"""v2 中保留的退役模块。

MessageBus 已经不是活跃设计的一部分，新的控制面由 Gateway + DeliveryEngine 承担。
"""

from __future__ import annotations

from tyrant_core.models import UnsupportedLegacyComponentError


class MessageBus:
    """退役后的兼容性哨兵。"""

    def __init__(self, *args, **kwargs) -> None:
        raise UnsupportedLegacyComponentError(
            "MessageBus 已在 v2 中退役；请改用 GatewayServer + DeliveryEngine。"
        )
