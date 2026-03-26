"""创建器注册表。"""

from __future__ import annotations

from tyrant_core.models import ActorBlueprint, ValidationError

from .base import ActorProvisioner


class ProvisionerRegistry:
    """按 hint 或 blueprint 选择具体 provisioner。"""

    def __init__(self, provisioners: list[ActorProvisioner] | None = None) -> None:
        self._provisioners: dict[str, ActorProvisioner] = {}
        for provisioner in provisioners or []:
            self.register(provisioner)

    def register(self, provisioner: ActorProvisioner) -> None:
        self._provisioners[provisioner.name] = provisioner

    async def resolve(
        self,
        blueprint: ActorBlueprint,
        *,
        hint: str | None = None,
    ) -> ActorProvisioner:
        if hint is not None:
            provisioner = self._provisioners.get(hint)
            if provisioner is None:
                raise ValidationError(f"unknown provisioner: {hint}")
            return provisioner

        for provisioner in self._provisioners.values():
            if await provisioner.supports(blueprint):
                return provisioner
        raise ValidationError("no matching provisioner")
