"""外部 surface attachment 生命周期。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tyrant_core.gateway.server import GatewayServer


class SurfaceAttachmentLifecycle:
    """负责外部连接 attachment/presence 的上下线登记。"""

    def __init__(
        self,
        *,
        gateway: "GatewayServer",
        actor_id: str,
        attachment_id: str,
        kind: str,
        endpoint: dict[str, Any],
        capabilities: list[str],
        gateway_id: str,
    ) -> None:
        self.gateway = gateway
        self.actor_id = actor_id
        self.attachment_id = attachment_id
        self.kind = kind
        self.endpoint = dict(endpoint)
        self.capabilities = list(capabilities)
        self.gateway_id = gateway_id

    async def attach(self) -> None:
        await self.gateway.register_attachment({
            "id": self.attachment_id,
            "actor_id": self.actor_id,
            "kind": self.kind,
            "endpoint": dict(self.endpoint),
            "capabilities": list(self.capabilities),
            "bypass_policy": True,
        })
        await self.gateway.report_presence({
            "attachment_id": self.attachment_id,
            "actor_id": self.actor_id,
            "gateway_id": self.gateway_id,
            "status": "ONLINE",
            "leases": 0,
            "bypass_policy": True,
        })

    async def detach(self) -> None:
        await self.gateway.report_presence({
            "attachment_id": self.attachment_id,
            "actor_id": self.actor_id,
            "gateway_id": self.gateway_id,
            "status": "OFFLINE",
            "leases": 0,
            "bypass_policy": True,
        })
