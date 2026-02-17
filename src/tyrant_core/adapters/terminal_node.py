"""TerminalNode 适配器。终端进程作为 Node 接入通信网络（范式 2）。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import structlog

from tyrant_core.adapters.terminal import TerminalSession, TerminalSessionManager
from tyrant_core.models import Message, Node

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tyrant_core.api.system_api import SystemAPI
    from tyrant_core.bus.message_bus import MessageBus

logger = structlog.get_logger()


class TerminalNodeAdapter:
    """将终端进程包装为 TyrantCore Node。

    - stdout 按时间窗口聚合 → Message 发给关联 Agent（parent）
    - 收到 Message → stdin
    """

    def __init__(
        self,
        node: Node,
        session: TerminalSession,
        api: SystemAPI,
        message_bus: MessageBus,
        *,
        aggregation_ms: int = 500,
    ) -> None:
        self.node = node
        self.session = session
        self.api = api
        self.message_bus = message_bus
        self.aggregation_ms = aggregation_ms
        self._parent_id: str | None = None

    async def run(self) -> None:
        """主循环：并行运行 stdout 读取和 inbox 监听。"""
        # 获取 parent（上级节点），作为 stdout 输出的接收者
        async with self.api.store.transaction():
            rel = await self.api.store.get_reports_to(self.node.id)
        if rel:
            self._parent_id = rel.node_a
        else:
            logger.warning(
                "terminal_node_no_parent",
                node_id=self.node.id,
            )

        try:
            await asyncio.gather(
                self._stdout_loop(),
                self._inbox_loop(),
            )
        except asyncio.CancelledError:
            logger.info("terminal_node_cancelled", node_id=self.node.id)
            raise

    async def _stdout_loop(self) -> None:
        """持续读取 stdout，按时间窗口聚合后发送 Message。"""
        while True:
            # 等待聚合时间窗口
            await asyncio.sleep(self.aggregation_ms / 1000.0)

            if not self.session.is_alive() and not self.session._buffer:
                # 进程已退出且无剩余输出
                if self._parent_id:
                    exit_msg = Message(
                        id=str(uuid4()),
                        timestamp=datetime.now(timezone.utc),
                        from_node=self.node.id,
                        to=self._parent_id,
                        type="TERMINAL_EXIT",
                        content={"text": "[进程已退出]"},
                    )
                    try:
                        await self.api.send_message(self.node.id, exit_msg)
                    except Exception:
                        logger.warning(
                            "terminal_exit_notify_failed",
                            node_id=self.node.id,
                        )
                break

            # 读取 buffer（非阻塞，立即返回当前内容）
            output = await self.session.read(timeout=0.1)
            if not output or not self._parent_id:
                continue

            # 发送 stdout 内容为 Message
            msg = Message(
                id=str(uuid4()),
                timestamp=datetime.now(timezone.utc),
                from_node=self.node.id,
                to=self._parent_id,
                type="TERMINAL_OUTPUT",
                content={"text": output},
            )
            try:
                await self.api.send_message(self.node.id, msg)
            except Exception:
                logger.warning(
                    "terminal_output_send_failed",
                    node_id=self.node.id,
                )

    async def _inbox_loop(self) -> None:
        """监听 inbox，收到 Message 写入 stdin。"""
        async for message in self.message_bus.subscribe_inbox(self.node.id):
            try:
                text = ""
                if message.content:
                    text = message.content.get("text", "")
                if text:
                    await self.session.write(text)
            except Exception:
                logger.warning(
                    "terminal_stdin_write_failed",
                    node_id=self.node.id,
                    message_id=message.id,
                )
