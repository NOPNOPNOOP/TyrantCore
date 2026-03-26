"""CLI 输出渲染。"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


class CliRenderer:
    """负责 CLI 的所有展示逻辑。"""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render_banner(self) -> None:
        self.console.print(
            Panel(
                "TyrantCore v2 CLI\n/help 查看命令",
                title="Gateway Control Plane",
            )
        )

    def render_info(self, message: str, *, style: str | None = None) -> None:
        self.console.print(message, style=style)

    def render_help(self) -> None:
        table = Table(title="Commands")
        table.add_column("命令")
        table.add_column("说明")
        table.add_row("/actors", "列出 Actor")
        table.add_row("/create <kind> <actor_id> <name>", "创建 Actor")
        table.add_row("/target <actor_id>", "设置默认聊天目标")
        table.add_row("/send <text>", "向当前目标发送文本")
        table.add_row("/inbox", "领取并展示 Tyrant 待处理消息")
        table.add_row("/bindings", "列出 CLI bindings")
        table.add_row("/sessions", "列出 CLI sessions")
        table.add_row("/deliveries [actor_id]", "查看 delivery 状态")
        table.add_row("/quit", "退出")
        self.console.print(table)

    def render_actors(self, actors: list[dict[str, Any]]) -> None:
        table = Table(title="Actors")
        table.add_column("ID")
        table.add_column("Kind")
        table.add_column("Name")
        table.add_column("Slug")
        for actor in actors:
            table.add_row(
                actor["id"],
                actor["kind"],
                actor["name"],
                actor["slug"],
            )
        self.console.print(table)

    def render_inbox(self, messages: list[dict[str, Any]]) -> None:
        if not messages:
            self.console.print("inbox is empty", style="dim")
            return

        for item in messages:
            envelope = item["envelope"]
            text = envelope["payload"].get("text", "")
            self.console.print(
                f"[{envelope['sender_actor_id']}] {text}",
                style="bold",
                markup=False,
            )

    def render_sessions(self, sessions: list[dict[str, Any]]) -> None:
        table = Table(title="Sessions")
        table.add_column("ID")
        table.add_column("Actor")
        table.add_column("External Key")
        table.add_column("Thread")
        table.add_column("State")
        for session in sessions:
            table.add_row(
                session["id"],
                session["actor_id"],
                session["external_key"],
                session.get("thread_id") or "-",
                session["state"],
            )
        self.console.print(table)

    def render_bindings(self, bindings: list[dict[str, Any]]) -> None:
        table = Table(title="Bindings")
        table.add_column("ID")
        table.add_column("Actor")
        table.add_column("External Key")
        table.add_column("Policy")
        for binding in bindings:
            table.add_row(
                binding.get("binding_id") or "-",
                binding["actor_id"],
                binding.get("external_key") or "*",
                binding["thread_policy"],
            )
        self.console.print(table)

    def render_deliveries(self, deliveries: list[dict[str, Any]]) -> None:
        table = Table(title="Deliveries")
        table.add_column("ID")
        table.add_column("Recipient")
        table.add_column("Envelope")
        table.add_column("State")
        table.add_column("Attempts")
        for delivery in deliveries:
            table.add_row(
                delivery["id"],
                delivery["recipient_actor_id"],
                delivery["envelope_id"],
                delivery["state"],
                str(delivery["attempt_count"]),
            )
        self.console.print(table)
