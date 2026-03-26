"""Gateway 管理器。"""

from __future__ import annotations

from typing import Protocol


class ManagedService(Protocol):
    """可被 GatewayManager 托管生命周期的服务协议。"""

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


Connector = ManagedService


class GatewayManager:
    """统一管理托管服务生命周期。"""

    def __init__(self) -> None:
        self._services: dict[str, ManagedService] = {}

    def register(self, service: ManagedService) -> None:
        self._services[service.name] = service

    def list_connectors(self) -> list[str]:
        return self.list_services()

    def list_services(self) -> list[str]:
        return sorted(self._services)

    async def start_all(self) -> None:
        for service in self._services.values():
            await service.start()

    async def stop_all(self) -> None:
        for service in self._services.values():
            await service.stop()
