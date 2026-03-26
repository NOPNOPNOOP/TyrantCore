"""外部 surface ingress connector 基类。"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from tyrant_core.models import ValidationError

if TYPE_CHECKING:
    from tyrant_core.gateway import GatewayServer


class BoundIngressConnector:
    """把外部输入接成 binding/session/envelope 的通用 connector。"""

    def __init__(
        self,
        gateway: "GatewayServer",
        *,
        surface: str,
        name: str,
        default_kind: str,
    ) -> None:
        self.gateway = gateway
        self.surface = surface
        self.name = name
        self.default_kind = default_kind

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def ingest_payload(
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
        effective_recipients = self._normalize_recipients(
            recipient_actor_id=recipient_actor_id,
            recipient_actor_ids=recipient_actor_ids,
        )
        effective_sender_actor_id, binding_id = await self._resolve_sender(
            external_key=external_key,
            sender_actor_id=sender_actor_id,
            account_id=account_id,
        )
        session_metadata = dict(metadata or {})
        if account_id is not None:
            session_metadata.setdefault("account_id", account_id)
        session = await self.gateway.open_session(
            surface=self.surface,
            actor_id=effective_sender_actor_id,
            external_key=external_key,
            thread_id=thread_id,
            binding_id=binding_id,
            metadata=session_metadata or None,
        )
        effective_thread_id = thread_id or session.thread_id
        result = await self.gateway.ingest({
            "sender_actor_id": effective_sender_actor_id,
            "recipient_actor_ids": effective_recipients,
            "thread_id": effective_thread_id,
            "payload": dict(payload),
            "kind": kind or self.default_kind,
            "subject": subject or external_key,
            "caller_actor_id": effective_sender_actor_id,
            "bypass_policy": bypass_policy,
        })
        if session.thread_id is None:
            await self.gateway.bind_session_thread(session.id, result["thread_id"])
        return result

    def _normalize_recipients(
        self,
        *,
        recipient_actor_id: str | None,
        recipient_actor_ids: list[str] | None,
    ) -> list[str]:
        if recipient_actor_id is not None and recipient_actor_ids is not None:
            raise ValidationError(
                "recipient_actor_id and recipient_actor_ids cannot be used together"
            )
        if recipient_actor_ids is not None:
            if not recipient_actor_ids:
                raise ValidationError("recipient_actor_ids cannot be empty")
            return list(recipient_actor_ids)
        if recipient_actor_id is None:
            raise ValidationError(
                "recipient_actor_id or recipient_actor_ids is required"
            )
        return [recipient_actor_id]

    async def _resolve_sender(
        self,
        *,
        external_key: str,
        sender_actor_id: str | None,
        account_id: str | None,
    ) -> tuple[str, str | None]:
        if sender_actor_id is not None:
            return sender_actor_id, None

        binding = await self.gateway.resolve_binding(
            surface=self.surface,
            external_key=external_key,
            account_id=account_id,
        )
        if binding is None:
            raise ValidationError(
                f"binding is not registered: {self.surface}:{external_key}"
            )
        return binding["actor_id"], binding.get("binding_id")
