"""Webhook connector。"""

from __future__ import annotations

from typing import Any

from tyrant_core.gateway import GatewayServer

from .base import BoundIngressConnector


class WebhookConnector(BoundIngressConnector):
    """把外部 webhook 事件接成 Gateway ingress。"""

    def __init__(self, gateway: GatewayServer, surface: str = "webhook") -> None:
        super().__init__(
            gateway,
            surface=surface,
            name="webhook",
            default_kind="WEBHOOK_EVENT",
        )

    async def receive_event(
        self,
        *,
        external_key: str,
        payload: dict[str, Any],
        recipient_actor_id: str | None = None,
        recipient_actor_ids: list[str] | None = None,
        sender_actor_id: str | None = None,
        account_id: str | None = None,
        thread_id: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
        metadata: dict[str, Any] | None = None,
        bypass_policy: bool = True,
    ) -> dict[str, Any]:
        return await self.ingest_payload(
            external_key=external_key,
            payload=payload,
            recipient_actor_id=recipient_actor_id,
            recipient_actor_ids=recipient_actor_ids,
            sender_actor_id=sender_actor_id,
            account_id=account_id,
            thread_id=thread_id,
            kind=kind,
            subject=subject,
            metadata=metadata,
            bypass_policy=bypass_policy,
        )
