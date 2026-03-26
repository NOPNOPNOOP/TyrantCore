"""终端 I/O pump。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tyrant_core.adapters.terminal import TerminalSession

if TYPE_CHECKING:
    from tyrant_core.gateway.server import GatewayServer


class TerminalStdoutPump:
    """把 terminal stdout 推送到 Gateway。"""

    def __init__(
        self,
        *,
        gateway: GatewayServer,
        actor_id: str,
        controller_actor_id: str,
        session: TerminalSession,
        aggregation_ms: int,
    ) -> None:
        self.gateway = gateway
        self.actor_id = actor_id
        self.controller_actor_id = controller_actor_id
        self.session = session
        self.aggregation_ms = aggregation_ms
        self._exit_notified = False

    async def run(self, thread_id: str | None = None) -> str | None:
        """持续转发 stdout，直到 terminal 退出。"""
        current_thread_id = thread_id
        while True:
            output = await self.session.read(timeout=self.aggregation_ms / 1000.0)
            if output:
                result = await self.gateway.ingest({
                    "sender_actor_id": self.actor_id,
                    "recipient_actor_ids": [self.controller_actor_id],
                    "thread_id": current_thread_id,
                    "kind": "TERMINAL_OUTPUT",
                    "payload": {
                        "text": output,
                        "session_id": self.session.session_id,
                    },
                    "bypass_policy": True,
                })
                current_thread_id = result["thread_id"]
                continue

            if self.session.is_alive():
                continue

            if not self._exit_notified:
                result = await self.gateway.ingest({
                    "sender_actor_id": self.actor_id,
                    "recipient_actor_ids": [self.controller_actor_id],
                    "thread_id": current_thread_id,
                    "kind": "TERMINAL_EXIT",
                    "payload": {
                        "text": "[terminal exited]",
                        "session_id": self.session.session_id,
                    },
                    "bypass_policy": True,
                })
                current_thread_id = result["thread_id"]
                self._exit_notified = True
            return current_thread_id


class TerminalStdinPump:
    """把 Gateway 下行消息写回 terminal stdin。"""

    def __init__(
        self,
        *,
        gateway: GatewayServer,
        actor_id: str,
        consumer_id: str,
        session: TerminalSession,
    ) -> None:
        self.gateway = gateway
        self.actor_id = actor_id
        self.consumer_id = consumer_id
        self.session = session

    async def run(self) -> None:
        """持续消费下行消息并写入 stdin。"""
        while True:
            leased = await self.gateway.lease_deliveries(
                actor_id=self.actor_id,
                consumer_id=self.consumer_id,
                limit=10,
            )
            if not leased:
                if not self.session.is_alive():
                    return
                await asyncio.sleep(0.2)
                continue

            for item in leased:
                delivery = item["delivery"]
                envelope = await self.gateway.get_envelope(delivery["envelope_id"])
                payload = envelope.get("payload", {})
                text = payload.get("text") or payload.get("input") or ""
                try:
                    if text:
                        await self.session.write(text)
                    await self.gateway.ack_delivery(delivery["id"])
                except Exception as exc:
                    await self.gateway.fail_delivery(
                        delivery["id"],
                        error={
                            "error": str(exc),
                            "adapter": "terminal",
                            "session_id": self.session.session_id,
                        },
                    )
                    raise
