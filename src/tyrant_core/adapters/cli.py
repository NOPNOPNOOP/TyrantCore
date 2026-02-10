"""CLI 适配器。Tyrant（人类）的终端交互入口。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import structlog
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tyrant_core.models import (
    Endpoint,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Permissions,
    Priority,
)
from tyrant_core.api.system_api import SystemAPI
from tyrant_core.bus.message_bus import MessageBus

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tyrant_core.runtime.agent_runtime import AgentRuntime

logger = structlog.get_logger()

COMMANDS = [
    "/talk", "/target", "/inbox", "/nodes", "/relations",
    "/tasks", "/create", "/grant", "/inspect", "/events",
    "/help", "/quit",
]


class CliAdapter:
    """Tyrant 的终端交互入口。"""

    def __init__(
        self,
        api: SystemAPI,
        message_bus: MessageBus,
        tyrant_node_id: str,
        runtime: AgentRuntime | None = None,
    ) -> None:
        self.api = api
        self.message_bus = message_bus
        self.tyrant_node_id = tyrant_node_id
        self.runtime = runtime
        self.console = Console()
        self.prompt_session = PromptSession(
            completer=WordCompleter(COMMANDS, sentence=True),
        )
        self.target: str | None = None  # 当前对话目标

    # ── 主循环 ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """主循环：初始化 Tyrant 节点，并行运行输入处理和消息接收。"""
        await self._ensure_tyrant_node()

        self.console.print(Panel(
            "TyrantCore v0.1 — 输入 /help 查看命令",
            style="bold green",
        ))

        await asyncio.gather(
            self._input_loop(),
            self._receive_loop(),
        )

    async def _ensure_tyrant_node(self) -> None:
        """确保 Tyrant 节点存在。"""
        async with self.api.store.transaction():
            tyrant = await self.api.store.get_node(self.tyrant_node_id)
        if not tyrant:
            now = datetime.now(timezone.utc)
            tyrant = Node(
                id=self.tyrant_node_id,
                type=NodeType.HUMAN,
                name="Tyrant",
                is_owner=True,
                endpoint=Endpoint(protocol="cli", address="local"),
                status=NodeStatus.ONLINE,
                permissions=Permissions(
                    create_agent=True,
                    destroy_agent=True,
                    recruit=True,
                    audit=True,
                    grant_permission=True,
                ),
                created_at=now,
            )
            async with self.api.store.transaction():
                await self.api.store.save_node(tyrant)

    # ── 节点 ID 解析 ────────────────────────────────────────────────────────

    async def _resolve_node_id(self, hint: str) -> str:
        """通过名称或 ID 前缀解析完整节点 ID。"""
        async with self.api.store.transaction():
            nodes = await self.api.store.list_nodes()

        # 精确匹配名称
        for n in nodes:
            if n.name == hint:
                return n.id

        # ID 前缀匹配
        matches = [n for n in nodes if n.id.startswith(hint)]
        if len(matches) == 1:
            return matches[0].id
        if len(matches) > 1:
            names = ", ".join(f"{m.name}({m.id[:12]})" for m in matches)
            raise ValueError(f"ID 前缀 '{hint}' 匹配到多个节点: {names}")

        raise ValueError(f"找不到节点: {hint}")

    # ── 输入处理 ───────────────────────────────────────────────────────────

    async def _input_loop(self) -> None:
        while True:
            try:
                line = await self.prompt_session.prompt_async("TyrantCore> ")
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                await self._handle_command(line)
            elif self.target:
                await self._send_chat(self.target, line)
            else:
                self.console.print(
                    "[请先用 /target <node_id> 设置对话目标]",
                    style="yellow",
                )

    async def _handle_command(self, line: str) -> None:
        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()

        try:
            if cmd == "/quit":
                raise KeyboardInterrupt
            elif cmd == "/help":
                self._show_help()
            elif cmd == "/target":
                await self._cmd_target(parts)
            elif cmd == "/talk":
                await self._cmd_talk(parts)
            elif cmd == "/inbox":
                await self._cmd_inbox()
            elif cmd == "/nodes":
                await self._cmd_nodes()
            elif cmd == "/relations":
                await self._cmd_relations()
            elif cmd == "/tasks":
                await self._cmd_tasks()
            elif cmd == "/create":
                await self._cmd_create(parts)
            elif cmd == "/grant":
                await self._cmd_grant(parts)
            elif cmd == "/inspect":
                await self._cmd_inspect(parts)
            elif cmd == "/events":
                await self._cmd_events(parts)
            else:
                self.console.print(f"未知命令: {cmd}", style="red")
        except Exception as e:
            self.console.print(f"错误: {e}", style="red")

    # ── 命令实现 ───────────────────────────────────────────────────────────

    def _show_help(self) -> None:
        table = Table(title="命令列表")
        table.add_column("命令", style="cyan")
        table.add_column("说明")
        table.add_row("/talk <node_id> <text>", "向指定节点发送 CHAT 消息")
        table.add_row("/target <node_id>", "设置默认对话目标")
        table.add_row("/inbox", "查看收件箱")
        table.add_row("/nodes", "列出所有节点")
        table.add_row("/relations", "列出所有关系")
        table.add_row("/tasks", "列出所有任务")
        table.add_row("/create <name> <instruction>", "创建 Agent")
        table.add_row("/grant <node_id> <permission>", "授予权限")
        table.add_row("/inspect <node_id>", "审计查看节点状态")
        table.add_row("/events [hours]", "查看事件日志（默认最近 1 小时）")
        table.add_row("/help", "显示帮助")
        table.add_row("/quit", "退出")
        self.console.print(table)

    async def _cmd_target(self, parts: list[str]) -> None:
        if len(parts) < 2:
            if self.target:
                self.console.print(f"当前目标: {self.target}")
            else:
                self.console.print("用法: /target <node_id|name>", style="yellow")
            return
        self.target = await self._resolve_node_id(parts[1])
        self.console.print(f"对话目标已设为: {self.target[:12]}", style="green")

    async def _cmd_talk(self, parts: list[str]) -> None:
        if len(parts) < 3:
            self.console.print("用法: /talk <node_id|name> <text>", style="yellow")
            return
        node_id = await self._resolve_node_id(parts[1])
        text = parts[2]
        await self._send_chat(node_id, text)

    async def _send_chat(self, to: str, text: str) -> None:
        msg = Message(
            id=str(uuid4()),
            timestamp=datetime.now(timezone.utc),
            from_node=self.tyrant_node_id,
            to=to,
            type="CHAT",
            content={"text": text},
            priority=Priority.NORMAL,
        )
        ids = await self.api.send_message(self.tyrant_node_id, msg)
        self.console.print(f"已发送 ({len(ids)} 条)", style="dim")

    async def _cmd_inbox(self) -> None:
        async with self.api.store.transaction():
            messages = await self.message_bus.get_inbox(self.tyrant_node_id)
        if not messages:
            self.console.print("收件箱为空", style="dim")
            return
        for msg in messages:
            self._display_message(msg)

    async def _cmd_nodes(self) -> None:
        async with self.api.store.transaction():
            nodes = await self.api.store.list_nodes()
        table = Table(title="节点列表")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("名称")
        table.add_column("类型")
        table.add_column("状态")
        table.add_column("Owner")
        for n in nodes:
            table.add_row(
                n.id[:12], n.name, n.type.value, n.status.value,
                "✓" if n.is_owner else "",
            )
        self.console.print(table)

    async def _cmd_relations(self) -> None:
        # 列出所有 tyrant 子树的关系
        async with self.api.store.transaction():
            nodes = await self.api.store.list_nodes()
            all_rels = []
            seen = set()
            for n in nodes:
                rels = await self.api.store.get_relations_for_node(n.id)
                for r in rels:
                    if r.id not in seen:
                        seen.add(r.id)
                        all_rels.append(r)
        table = Table(title="关系列表")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("类型")
        table.add_column("Node A")
        table.add_column("Node B")
        for r in all_rels:
            table.add_row(r.id[:12], r.type.value, r.node_a[:12], r.node_b[:12])
        self.console.print(table)

    async def _cmd_tasks(self) -> None:
        tasks = await self.api.task_query(caller_id=self.tyrant_node_id)
        if not tasks:
            self.console.print("无任务", style="dim")
            return
        table = Table(title="任务列表")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("状态")
        table.add_column("创建者")
        table.add_column("执行者")
        table.add_column("内容")
        for t in tasks:
            content_str = json.dumps(t.content, ensure_ascii=False)[:40]
            table.add_row(
                t.id[:12], t.status.value, t.creator[:12],
                t.assignee[:12], content_str,
            )
        self.console.print(table)

    async def _cmd_create(self, parts: list[str]) -> None:
        if len(parts) < 3:
            self.console.print(
                "用法: /create <name> <instruction>", style="yellow"
            )
            return
        name = parts[1]
        instruction = parts[2]
        node = await self.api.create_agent(
            caller_id=self.tyrant_node_id,
            name=name,
            parent_id=self.tyrant_node_id,
            agent_config={"instruction": instruction},
        )
        # 启动 agent runtime
        if self.runtime:
            await self.runtime.start_agent(node)
        self.console.print(
            f"Agent 已创建: {node.name} ({node.id[:12]})", style="green"
        )

    async def _cmd_grant(self, parts: list[str]) -> None:
        if len(parts) < 3:
            self.console.print(
                "用法: /grant <node_id|name> <permission>", style="yellow"
            )
            return
        node_id = await self._resolve_node_id(parts[1])
        permission = parts[2]
        await self.api.grant_permission(
            caller_id=self.tyrant_node_id,
            target_id=node_id,
            permission=permission,
        )
        self.console.print(
            f"已授予 {node_id[:12]} 权限: {permission}", style="green"
        )

    async def _cmd_inspect(self, parts: list[str]) -> None:
        if len(parts) < 2:
            self.console.print("用法: /inspect <node_id|name>", style="yellow")
            return
        node_id = await self._resolve_node_id(parts[1])
        result = await self.api.audit_inspect(
            caller_id=self.tyrant_node_id, target_id=node_id
        )
        self.console.print(Panel(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            title=f"审计: {node_id[:12]}",
            border_style="magenta",
        ))

    async def _cmd_events(self, parts: list[str]) -> None:
        hours = float(parts[1]) if len(parts) > 1 else 1.0
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self.api.store.transaction():
            events = await self.api.store.get_events(since=since)
        if not events:
            self.console.print("无事件", style="dim")
            return
        table = Table(title=f"事件日志（最近 {hours} 小时）")
        table.add_column("时间")
        table.add_column("类型", style="cyan")
        table.add_column("来源")
        table.add_column("数据")
        for e in events:
            data_str = json.dumps(e.data, ensure_ascii=False)[:50]
            table.add_row(
                str(e.timestamp)[:19], e.type, e.source[:12], data_str,
            )
        self.console.print(table)

    # ── 消息接收 ───────────────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        async for message in self.message_bus.subscribe_inbox(
            self.tyrant_node_id
        ):
            self._display_message(message)
            if message.requires_ack:
                await self.message_bus.ack(message.id, self.tyrant_node_id)

    def _display_message(self, message: Message) -> None:
        """用 rich 格式化消息输出。"""
        title = f"📨 [{message.type}] from {message.from_node}"
        if "text" in message.content:
            body = message.content["text"]
        else:
            body = json.dumps(message.content, ensure_ascii=False, indent=2)
        self.console.print(Panel(body, title=title, border_style="cyan"))
