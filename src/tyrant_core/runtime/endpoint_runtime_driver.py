"""声明式 endpoint runtime driver。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from tyrant_core.models import (
    ActorControl,
    ActorProfile,
    Attachment,
    AttachmentKind,
    Presence,
    PresenceStatus,
    ValidationError,
)

from .runtime_driver import RuntimeDriver, RuntimeSpawnResult

if TYPE_CHECKING:
    from tyrant_core.gateway import GatewayServer


@dataclass(slots=True)
class EndpointRuntimeState:
    """记录声明式 endpoint runtime 的当前附着状态。"""

    endpoint: dict[str, Any]
    capabilities: list[str]
    gateway_id: str
    status: PresenceStatus


class EndpointRuntimeDriver(RuntimeDriver):
    """把 actor 声明为 attachment/presence 的通用 runtime driver。"""

    def __init__(
        self,
        *,
        runtime_name: str,
        driver_name: str,
        config_keys: tuple[str, ...],
        attachment_kind: AttachmentKind,
        gateway: "GatewayServer" | None = None,
        gateway_id: str,
        default_capabilities: list[str] | None = None,
        default_status: PresenceStatus = PresenceStatus.ONLINE,
    ) -> None:
        self.runtime_name = runtime_name
        self.driver_name = driver_name
        self.config_keys = config_keys
        self.attachment_kind = attachment_kind
        self.gateway = gateway
        self.gateway_id = gateway_id
        self.default_capabilities = list(default_capabilities or [])
        self.default_status = default_status
        self._states: dict[str, EndpointRuntimeState] = {}

    def bind_gateway(self, gateway: "GatewayServer") -> None:
        self.gateway = gateway

    async def spawn(
        self,
        *,
        actor_id: str,
        control: ActorControl,
        profile: ActorProfile | None = None,
    ) -> RuntimeSpawnResult:
        existing = self._states.get(actor_id)
        if existing is not None:
            return self._build_spawn_result(actor_id=actor_id, state=existing)

        actor = {"id": actor_id, "metadata": {}}
        if self.gateway is not None:
            actor = await self.gateway.get_actor(actor_id)

        state = self._resolve_state(actor, control, profile)
        self._states[actor_id] = state
        return self._build_spawn_result(actor_id=actor_id, state=state)

    async def stop(self, *, actor_id: str) -> None:
        self._states.pop(actor_id, None)

    async def status(self, *, actor_id: str) -> dict | None:
        state = self._states.get(actor_id)
        if state is None:
            return None
        return {
            "actor_id": actor_id,
            "driver": self.driver_name,
            "endpoint": dict(state.endpoint),
            "capabilities": list(state.capabilities),
            "gateway_id": state.gateway_id,
            "presence_status": state.status.value,
            "running": True,
        }

    def _resolve_state(
        self,
        actor: dict[str, Any],
        control: ActorControl,
        profile: ActorProfile | None,
    ) -> EndpointRuntimeState:
        endpoint: dict[str, Any] = {}
        capabilities = list(self.default_capabilities)
        gateway_id = self.gateway_id
        status = self.default_status

        for candidate in self._iter_candidates(actor, control, profile):
            normalized = self._normalize_spec(candidate)
            endpoint.update(normalized["endpoint"])
            if "capabilities" in normalized:
                capabilities = normalized["capabilities"]
            if "gateway_id" in normalized:
                gateway_id = normalized["gateway_id"]
            if "status" in normalized:
                status = normalized["status"]

        if not endpoint:
            raise ValidationError(f"{self.runtime_name} runtime endpoint is missing")
        endpoint["driver"] = self.driver_name
        return EndpointRuntimeState(
            endpoint=endpoint,
            capabilities=capabilities,
            gateway_id=gateway_id,
            status=status,
        )

    def _iter_candidates(
        self,
        actor: dict[str, Any],
        control: ActorControl,
        profile: ActorProfile | None,
    ):
        if profile is not None:
            for key in self.config_keys:
                value = profile.bootstrap_policy.get(key)
                if value is not None:
                    yield value

        metadata = actor.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValidationError("actor.metadata must be a dict")

        for key in self.config_keys:
            value = metadata.get(key)
            if value is not None:
                yield value

        bootstrap = metadata.get("bootstrap", {})
        if bootstrap is not None and not isinstance(bootstrap, dict):
            raise ValidationError("actor.metadata.bootstrap must be a dict")
        if isinstance(bootstrap, dict):
            for key in self.config_keys:
                value = bootstrap.get(key)
                if value is not None:
                    yield value

        placement = control.placement
        if not isinstance(placement, dict):
            raise ValidationError("actor control placement must be a dict")
        for key in self.config_keys:
            value = placement.get(key)
            if value is not None:
                yield value

    def _normalize_spec(self, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            return {"endpoint": {"url": value}}
        if not isinstance(value, dict):
            raise ValidationError(
                f"{self.runtime_name} runtime config must be a string or dict"
            )

        data = dict(value)
        endpoint = data.pop("endpoint", None)
        if endpoint is None:
            endpoint_payload = data
        else:
            if not isinstance(endpoint, dict):
                raise ValidationError(
                    f"{self.runtime_name} runtime endpoint must be a dict"
                )
            endpoint_payload = dict(endpoint)
            endpoint_payload.update(data)

        normalized: dict[str, Any] = {
            "endpoint": {
                key: item
                for key, item in endpoint_payload.items()
                if key not in {"capabilities", "gateway_id", "status"}
            }
        }

        capabilities = value.get("capabilities")
        if capabilities is not None:
            if not isinstance(capabilities, list):
                raise ValidationError(
                    f"{self.runtime_name} runtime capabilities must be a list"
                )
            normalized["capabilities"] = [str(item) for item in capabilities]

        gateway_id = value.get("gateway_id")
        if gateway_id is not None:
            normalized["gateway_id"] = str(gateway_id)

        raw_status = value.get("status")
        if raw_status is not None:
            normalized["status"] = PresenceStatus(raw_status)

        return normalized

    def _build_spawn_result(
        self,
        *,
        actor_id: str,
        state: EndpointRuntimeState,
    ) -> RuntimeSpawnResult:
        now = datetime.now(timezone.utc)
        return RuntimeSpawnResult(
            attachment=Attachment(
                id=f"attachment:{actor_id}",
                actor_id=actor_id,
                kind=self.attachment_kind,
                endpoint=dict(state.endpoint),
                capabilities=list(state.capabilities),
                created_at=now,
            ),
            presence=Presence(
                attachment_id=f"attachment:{actor_id}",
                actor_id=actor_id,
                gateway_id=state.gateway_id,
                status=state.status,
                leases=0,
                last_heartbeat_at=now,
            ),
        )
