"""内置本地 runtime 行为。"""

from __future__ import annotations

from typing import Any

from tyrant_core.models import ActorControl, ActorProfile, ValidationError

from ..actor_runtime import DeliveryHandler


def build_echo_handler(
    actor: dict[str, Any],
    control: ActorControl,
    profile: ActorProfile | None,
    spec: dict[str, Any],
) -> DeliveryHandler:
    """返回把文本回显给发送者的 handler。"""
    del actor, control, profile
    transform = spec.get("transform", "identity")
    prefix = spec.get("prefix", "")
    suffix = spec.get("suffix", "")
    if transform not in {"identity", "upper", "lower"}:
        raise ValidationError(f"unsupported echo transform: {transform}")

    async def handler(envelope: dict[str, Any], delivery: dict[str, Any]) -> dict[str, Any]:
        del delivery
        text = str(envelope.get("payload", {}).get("text", ""))
        if transform == "upper":
            text = text.upper()
        elif transform == "lower":
            text = text.lower()
        payload = dict(envelope.get("payload", {}))
        payload["text"] = f"{prefix}{text}{suffix}"
        return {
            "recipient_actor_ids": [envelope["sender_actor_id"]],
            "payload": payload,
            "kind": spec.get("kind", envelope.get("kind", "CHAT")),
        }

    return handler


def build_template_reply_handler(
    actor: dict[str, Any],
    control: ActorControl,
    profile: ActorProfile | None,
    spec: dict[str, Any],
) -> DeliveryHandler:
    """返回固定模板回复 handler。"""
    del actor, control, profile
    payload = dict(spec.get("payload", {}))
    if "text" not in payload:
        text = spec.get("text")
        if text is None:
            raise ValidationError("template_reply requires text or payload.text")
        payload["text"] = text

    async def handler(envelope: dict[str, Any], delivery: dict[str, Any]) -> dict[str, Any]:
        del delivery
        return {
            "recipient_actor_ids": [envelope["sender_actor_id"]],
            "payload": dict(payload),
            "kind": spec.get("kind", "CHAT"),
        }

    return handler


def build_noop_handler(
    actor: dict[str, Any],
    control: ActorControl,
    profile: ActorProfile | None,
    spec: dict[str, Any],
) -> DeliveryHandler:
    """返回不产生输出的 handler。"""
    del actor, control, profile, spec

    async def handler(envelope: dict[str, Any], delivery: dict[str, Any]) -> None:
        del envelope, delivery
        return None

    return handler
