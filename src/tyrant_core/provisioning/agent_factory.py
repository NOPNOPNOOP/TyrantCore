"""默认 AgentFactory 兼容别名。"""

from __future__ import annotations

from .queue_processor import ProvisioningQueueProcessor


class AgentFactoryService(ProvisioningQueueProcessor):
    """兼容旧命名，真实职责已下沉到 ProvisioningQueueProcessor。"""
