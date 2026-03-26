"""运行时驱动注册表。"""

from __future__ import annotations

from tyrant_core.models import RuntimeKind, ValidationError

from .runtime_driver import RuntimeDriver


class RuntimeDriverRegistry:
    """按 runtime_kind 注册和解析驱动。"""

    def __init__(self) -> None:
        self._drivers: dict[RuntimeKind, RuntimeDriver] = {}

    def register(self, runtime_kind: str | RuntimeKind, driver: RuntimeDriver) -> None:
        self._drivers[RuntimeKind(runtime_kind)] = driver

    def get(self, runtime_kind: str | RuntimeKind) -> RuntimeDriver | None:
        return self._drivers.get(RuntimeKind(runtime_kind))

    def require(self, runtime_kind: str | RuntimeKind) -> RuntimeDriver:
        kind = RuntimeKind(runtime_kind)
        driver = self._drivers.get(kind)
        if driver is None:
            raise ValidationError(f"runtime driver is not registered: {kind.value}")
        return driver

    def list_runtime_kinds(self) -> list[str]:
        return sorted(kind.value for kind in self._drivers)


def build_single_runtime_driver_registry(
    driver: RuntimeDriver,
    *,
    runtime_kind: str | RuntimeKind = RuntimeKind.LOCAL,
) -> RuntimeDriverRegistry:
    """构造只含一个驱动的注册表。"""
    registry = RuntimeDriverRegistry()
    registry.register(runtime_kind, driver)
    return registry
