"""终端 attachment 生命周期控制。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tyrant_core.adapters.terminal import TerminalSession

if TYPE_CHECKING:
    from tyrant_core.gateway.server import GatewayServer


class TerminalAttachmentLifecycle:
    """负责 terminal attachment 的上下线登记。"""

    def __init__(
        self,
        *,
        gateway: GatewayServer,
        actor_id: str,
        attachment_id: str,
        session: TerminalSession,
        gateway_id: str,
    ) -> None:
        self.gateway = gateway
        self.actor_id = actor_id
        self.attachment_id = attachment_id
        self.session = session
        self.gateway_id = gateway_id

    async def attach(self) -> None:
        """注册 terminal attachment 和 online presence。"""
        await self.gateway.register_attachment({
            "id": self.attachment_id,
            "actor_id": self.actor_id,
            "kind": "TERMINAL",
            "endpoint": {
                "session_id": self.session.session_id,
                "command": self.session.command,
                "cwd": self.session.cwd,
            },
            "capabilities": ["stdin", "stdout", "signal"],
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
        """登记 offline presence。"""
        await self.gateway.report_presence({
            "attachment_id": self.attachment_id,
            "actor_id": self.actor_id,
            "gateway_id": self.gateway_id,
            "status": "OFFLINE",
            "leases": 0,
            "bypass_policy": True,
        })
