"""能力注册表。"""

from __future__ import annotations


class CapabilityRegistry:
    """记录 attachment / connector 暴露出的能力。"""

    def __init__(self) -> None:
        self._capabilities: dict[str, list[str]] = {}

    def register(self, owner_id: str, capabilities: list[str]) -> None:
        self._capabilities[owner_id] = list(capabilities)

    def get(self, owner_id: str) -> list[str]:
        return list(self._capabilities.get(owner_id, []))
