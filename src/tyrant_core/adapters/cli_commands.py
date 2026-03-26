"""CLI 命令处理。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tyrant_core.adapters.cli import CliAdapter


CLI_COMMANDS = [
    "/help",
    "/actors",
    "/create",
    "/target",
    "/send",
    "/inbox",
    "/bindings",
    "/sessions",
    "/deliveries",
    "/quit",
]


class CliCommandHandler:
    """协调命令解析、调用与渲染。"""

    def __init__(self, adapter: CliAdapter) -> None:
        self.adapter = adapter

    async def handle(self, line: str) -> bool:
        parts = line.split(maxsplit=3)
        command = parts[0].lower()

        if command == "/help":
            self.adapter.show_help()
            return True

        if command == "/actors":
            await self.adapter.show_actors()
            return True

        if command == "/create":
            if len(parts) < 4:
                self.adapter.show_info(
                    "用法: /create <kind> <actor_id> <name>",
                    style="yellow",
                )
                return True
            actor = await self.adapter.create_actor(
                kind=parts[1],
                actor_id=parts[2],
                name=parts[3],
            )
            self.adapter.show_created_actor(actor)
            return True

        if command == "/target":
            if len(parts) < 2:
                self.adapter.show_info("用法: /target <actor_id>", style="yellow")
                return True
            self.adapter.set_target(parts[1])
            return True

        if command == "/send":
            if len(parts) < 2:
                self.adapter.show_info("用法: /send <text>", style="yellow")
                return True
            result = await self.adapter.send_text(line.split(maxsplit=1)[1])
            self.adapter.show_send_result(result)
            return True

        if command == "/inbox":
            await self.adapter.show_inbox()
            return True

        if command == "/sessions":
            await self.adapter.show_sessions()
            return True

        if command == "/bindings":
            await self.adapter.show_bindings()
            return True

        if command == "/deliveries":
            actor_id = parts[1] if len(parts) >= 2 else None
            await self.adapter.show_deliveries(actor_id=actor_id)
            return True

        if command == "/quit":
            return False

        self.adapter.show_info(f"未知命令: {command}", style="yellow")
        return True
