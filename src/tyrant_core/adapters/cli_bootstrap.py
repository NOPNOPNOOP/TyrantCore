"""CLI 启动预热。"""

from __future__ import annotations

from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors.cli import CliConnector


class CliBootstrapper:
    """确保 CLI 对应的人类入口与 binding 就绪。"""

    def __init__(
        self,
        gateway: GatewayServer,
        *,
        connector: CliConnector,
        tyrant_actor_id: str,
        external_key: str,
    ) -> None:
        self.gateway = gateway
        self.connector = connector
        self.tyrant_actor_id = tyrant_actor_id
        self.external_key = external_key

    async def ensure_ready(self) -> None:
        actors = await self.gateway.list_actors(id=self.tyrant_actor_id)
        if not actors:
            await self.gateway.create_actor({
                "id": self.tyrant_actor_id,
                "kind": "TYRANT",
                "name": "Tyrant",
                "slug": self.tyrant_actor_id,
                "bypass_policy": True,
            })

        bindings = await self.gateway.query_bindings(
            surface=self.connector.surface,
            actor_id=self.tyrant_actor_id,
            limit=100,
        )
        if any(binding.get("external_key") == self.external_key for binding in bindings):
            return

        await self.gateway.register_binding({
            "surface": self.connector.surface,
            "actor_id": self.tyrant_actor_id,
            "external_key": self.external_key,
            "thread_policy": "REUSE",
        })
