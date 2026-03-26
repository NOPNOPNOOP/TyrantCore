"""CLI connector。"""

from __future__ import annotations

from tyrant_core.gateway import GatewayServer


class CliConnector:
    name = "cli"

    def __init__(self, gateway: GatewayServer, surface: str = "cli") -> None:
        self.gateway = gateway
        self.surface = surface

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_text(
        self,
        *,
        sender_actor_id: str,
        recipient_actor_id: str,
        text: str,
        external_key: str,
        thread_id: str | None = None,
    ) -> dict:
        session = await self.gateway.open_session(
            surface=self.surface,
            actor_id=sender_actor_id,
            external_key=external_key,
            thread_id=thread_id,
        )
        effective_thread_id = thread_id or session.thread_id
        result = await self.gateway.ingest({
            "sender_actor_id": sender_actor_id,
            "recipient_actor_ids": [recipient_actor_id],
            "thread_id": effective_thread_id,
            "payload": {"text": text},
            "kind": "CHAT",
            "subject": external_key,
            "caller_actor_id": sender_actor_id,
        })
        if session.thread_id is None:
            await self.gateway.bind_session_thread(session.id, result["thread_id"])
        return result
