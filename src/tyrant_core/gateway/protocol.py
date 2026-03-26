"""Gateway 协议对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class GatewayHello:
    """连接建立时的握手信息。"""

    role: str
    client_id: str
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GatewayEnvelope:
    """Gateway 内部流转的规范化信封。"""

    envelope_id: str
    actor_id: str
    surface: str
    payload: dict[str, Any] = field(default_factory=dict)
    thread_id: str | None = None
    received_at: datetime | None = None


@dataclass(slots=True)
class GatewayLeaseRequest:
    """消费端申请领取待处理投递。"""

    consumer_id: str
    actor_id: str
    limit: int = 1
    lease_seconds: int = 30
