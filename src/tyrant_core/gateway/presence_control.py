"""Gateway presence / attachment 子控制器。"""

from __future__ import annotations

from typing import Any

from tyrant_core.api import SystemAPI


class GatewayPresenceControl:
    """负责 attachment 与 presence 的登记和查询。"""

    def __init__(self, api: SystemAPI) -> None:
        self.api = api

    async def register_attachment(self, spec: dict[str, Any]) -> dict[str, Any]:
        return await self.api.register_attachment(spec)

    async def report_presence(self, spec: dict[str, Any]) -> dict[str, Any]:
        return await self.api.report_presence(spec)

    async def query_attachments(
        self,
        *,
        actor_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.api.query_attachments(
            actor_id=actor_id,
            kind=kind,
            limit=limit,
        )

    async def get_presence(self, attachment_id: str) -> dict[str, Any] | None:
        return await self.api.get_presence(attachment_id)
