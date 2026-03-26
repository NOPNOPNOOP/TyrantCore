"""v2 CLI 适配器。"""

from __future__ import annotations

from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from rich.console import Console

from tyrant_core.adapters.cli_bootstrap import CliBootstrapper
from tyrant_core.adapters.cli_commands import CLI_COMMANDS, CliCommandHandler
from tyrant_core.adapters.cli_render import CliRenderer
from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors.cli import CliConnector


class CliAdapter:
    """Gateway 驱动的人类 CLI 入口。"""

    def __init__(
        self,
        gateway: GatewayServer,
        *,
        tyrant_actor_id: str = "tyrant",
        external_key: str = "cli:default",
        console: Console | None = None,
        prompt_session: PromptSession | None = None,
    ) -> None:
        self.gateway = gateway
        self.tyrant_actor_id = tyrant_actor_id
        self.external_key = external_key
        self.renderer = CliRenderer(console)
        self.console = self.renderer.console
        self.prompt_session = prompt_session or PromptSession(
            completer=WordCompleter(CLI_COMMANDS, sentence=True),
        )
        self.connector = CliConnector(gateway)
        self.bootstrapper = CliBootstrapper(
            gateway,
            connector=self.connector,
            tyrant_actor_id=self.tyrant_actor_id,
            external_key=self.external_key,
        )
        self.commands = CliCommandHandler(self)
        self.target_actor_id: str | None = None
        self.consumer_id = f"cli-consumer:{tyrant_actor_id}"

    async def bootstrap(self) -> None:
        """确保人类入口 Actor 存在。"""
        await self.bootstrapper.ensure_ready()

    async def create_actor(
        self,
        *,
        actor_id: str,
        kind: str,
        name: str,
        slug: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        result = await self.gateway.create_actor({
            "id": actor_id,
            "kind": kind,
            "name": name,
            "slug": slug or actor_id,
            "tags": tags or [],
            "caller_actor_id": self.tyrant_actor_id,
        })
        return result["actor"]

    async def send_text(
        self,
        text: str,
        *,
        recipient_actor_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        target = recipient_actor_id or self.target_actor_id
        if target is None:
            raise ValueError("尚未设置 target_actor_id")
        return await self.connector.send_text(
            sender_actor_id=self.tyrant_actor_id,
            recipient_actor_id=target,
            text=text,
            external_key=self.external_key,
            thread_id=thread_id,
        )

    async def receive_once(self, limit: int = 20) -> list[dict[str, Any]]:
        leased = await self.gateway.lease_deliveries(
            actor_id=self.tyrant_actor_id,
            consumer_id=self.consumer_id,
            limit=limit,
        )
        messages: list[dict[str, Any]] = []
        for item in leased:
            delivery = item["delivery"]
            envelope = await self.gateway.get_envelope(delivery["envelope_id"])
            await self.gateway.ack_delivery(delivery["id"])
            messages.append({
                "delivery": delivery,
                "envelope": envelope,
            })
        return messages

    async def list_actors(self) -> list[dict[str, Any]]:
        return await self.gateway.list_actors()

    async def list_sessions(self) -> list[dict[str, Any]]:
        return await self.gateway.query_sessions(
            surface=self.connector.surface,
            limit=100,
        )

    async def list_bindings(self) -> list[dict[str, Any]]:
        return await self.gateway.query_bindings(
            surface=self.connector.surface,
            limit=100,
        )

    async def list_deliveries(
        self,
        actor_id: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.gateway.query_deliveries(
            actor_id=actor_id,
            state=state,
            limit=100,
        )

    def show_info(self, message: str, *, style: str | None = None) -> None:
        self.renderer.render_info(message, style=style)

    def show_help(self) -> None:
        self.renderer.render_help()

    def show_created_actor(self, actor: dict[str, Any]) -> None:
        self.show_info(
            f"created actor={actor['id']} kind={actor['kind']}",
            style="green",
        )

    def show_send_result(self, result: dict[str, Any]) -> None:
        self.show_info(
            f"sent thread={result['thread_id']} envelope={result['envelope']['id']}",
            style="green",
        )

    def set_target(self, actor_id: str) -> None:
        self.target_actor_id = actor_id
        self.show_info(f"target={self.target_actor_id}", style="cyan")

    async def run(self) -> None:
        """运行交互式 CLI。"""
        await self.gateway.start()
        await self.bootstrap()
        self.renderer.render_banner()
        try:
            while True:
                line = await self.prompt_session.prompt_async("TyrantCore> ")
                line = line.strip()
                if not line:
                    continue
                if line.startswith("/"):
                    should_continue = await self._handle_command(line)
                    if not should_continue:
                        break
                    continue
                try:
                    result = await self.send_text(line)
                except ValueError as exc:
                    self.show_info(str(exc), style="yellow")
                    continue
                self.show_send_result(result)
        finally:
            await self.gateway.stop()

    async def _handle_command(self, line: str) -> bool:
        return await self.commands.handle(line)

    async def show_actors(self) -> None:
        actors = await self.list_actors()
        self.renderer.render_actors(actors)

    async def show_inbox(self) -> None:
        messages = await self.receive_once()
        self.renderer.render_inbox(messages)

    async def show_sessions(self) -> None:
        sessions = await self.list_sessions()
        self.renderer.render_sessions(sessions)

    async def show_bindings(self) -> None:
        bindings = await self.list_bindings()
        self.renderer.render_bindings(bindings)

    async def show_deliveries(self, actor_id: str | None) -> None:
        deliveries = await self.list_deliveries(actor_id=actor_id)
        self.renderer.render_deliveries(deliveries)
