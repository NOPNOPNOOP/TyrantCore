"""runtime 相关的标准组装器。"""

from __future__ import annotations

from dataclasses import dataclass

from tyrant_core.adapters.terminal import TerminalSessionManager
from tyrant_core.api import SystemAPI
from tyrant_core.gateway import GatewayServer
from tyrant_core.gateway.connectors.terminal_bridge import TerminalNodeAdapter
from tyrant_core.management import ActorReconciler, ActorRecoveryService
from tyrant_core.models import RuntimeKind
from tyrant_core.store import SqliteStore, Store

from .driver_registry import RuntimeDriverRegistry
from .external_runtime_driver import ExternalRuntimeDriver
from .local_runtime import (
    LocalRuntimeBehaviorRegistry,
    build_builtin_local_runtime_behavior_registry,
)
from .local_runtime_driver import LocalHandlerFactory, LocalRuntimeDriver
from .remote_runtime_driver import RemoteRuntimeDriver
from .terminal_runtime_driver import TerminalAdapterFactory, TerminalRuntimeDriver
from .runtime_supervisor import RuntimeSupervisor


@dataclass(slots=True)
class LocalRuntimeGatewayStack:
    """标准本地 runtime 组装结果。"""

    gateway: GatewayServer
    driver: LocalRuntimeDriver
    runtime_drivers: RuntimeDriverRegistry
    supervisor: RuntimeSupervisor
    reconciler: ActorReconciler
    recovery_service: ActorRecoveryService


@dataclass(slots=True)
class EmbeddedRuntimeGatewayStack:
    """标准 embedded runtime 组装结果。"""

    gateway: GatewayServer
    runtime_drivers: RuntimeDriverRegistry
    supervisor: RuntimeSupervisor
    reconciler: ActorReconciler
    recovery_service: ActorRecoveryService
    local_driver: LocalRuntimeDriver | None = None
    terminal_driver: TerminalRuntimeDriver | None = None
    remote_driver: RemoteRuntimeDriver | None = None
    external_driver: ExternalRuntimeDriver | None = None


def build_local_runtime_gateway(
    *,
    store: Store | None = None,
    database_url: str | None = None,
    handler_factory: LocalHandlerFactory | None = None,
    behavior_registry: LocalRuntimeBehaviorRegistry | None = None,
    runtime_drivers: RuntimeDriverRegistry | None = None,
    recover_on_start: bool = True,
    recovery_limit: int = 1000,
    recovery_strict: bool = False,
    poll_interval: float = 1.0,
    lease_seconds: int = 30,
    batch_size: int = 1,
    gateway_id: str = "gateway:local-runtime",
) -> LocalRuntimeGatewayStack:
    """组装带默认本地 runtime 的 GatewayServer。"""
    effective_store = store or SqliteStore(database_url=database_url)
    effective_registry = behavior_registry or build_builtin_local_runtime_behavior_registry()
    driver = LocalRuntimeDriver(
        handler_factory=handler_factory,
        behavior_registry=effective_registry,
        poll_interval=poll_interval,
        lease_seconds=lease_seconds,
        batch_size=batch_size,
        gateway_id=gateway_id,
    )
    effective_runtime_drivers = runtime_drivers or RuntimeDriverRegistry()
    effective_runtime_drivers.register(RuntimeKind.LOCAL, driver)
    supervisor = RuntimeSupervisor(effective_runtime_drivers, store=effective_store)
    reconciler = ActorReconciler(supervisor, store=effective_store)
    recovery_service = ActorRecoveryService(
        reconciler,
        limit=recovery_limit,
        strict=recovery_strict,
    )
    api = SystemAPI(
        store=effective_store,
        runtime_supervisor=supervisor,
        reconciler=reconciler,
    )
    gateway = GatewayServer(
        store=effective_store,
        api=api,
    )
    driver.bind_gateway(gateway)
    gateway.register_managed_service(supervisor)
    if recover_on_start:
        gateway.register_managed_service(recovery_service)
    return LocalRuntimeGatewayStack(
        gateway=gateway,
        driver=driver,
        runtime_drivers=effective_runtime_drivers,
        supervisor=supervisor,
        reconciler=reconciler,
        recovery_service=recovery_service,
    )


def build_embedded_runtime_gateway(
    *,
    store: Store | None = None,
    database_url: str | None = None,
    enable_local: bool = True,
    enable_terminal: bool = True,
    enable_remote: bool = True,
    enable_external: bool = True,
    handler_factory: LocalHandlerFactory | None = None,
    behavior_registry: LocalRuntimeBehaviorRegistry | None = None,
    terminal_session_manager: TerminalSessionManager | None = None,
    terminal_adapter_factory: TerminalAdapterFactory | None = None,
    runtime_drivers: RuntimeDriverRegistry | None = None,
    recover_on_start: bool = True,
    recovery_limit: int = 1000,
    recovery_strict: bool = False,
    local_poll_interval: float = 1.0,
    local_lease_seconds: int = 30,
    local_batch_size: int = 1,
    local_gateway_id: str = "gateway:local-runtime",
    terminal_gateway_id: str = "gateway:terminal-runtime",
    terminal_aggregation_ms: int = 500,
) -> EmbeddedRuntimeGatewayStack:
    """组装带内置 runtime driver 集合的 GatewayServer。"""
    effective_store = store or SqliteStore(database_url=database_url)
    effective_runtime_drivers = runtime_drivers or RuntimeDriverRegistry()
    local_driver = None
    if enable_local:
        effective_registry = behavior_registry or build_builtin_local_runtime_behavior_registry()
        local_driver = LocalRuntimeDriver(
            handler_factory=handler_factory,
            behavior_registry=effective_registry,
            poll_interval=local_poll_interval,
            lease_seconds=local_lease_seconds,
            batch_size=local_batch_size,
            gateway_id=local_gateway_id,
        )
        effective_runtime_drivers.register(RuntimeKind.LOCAL, local_driver)

    terminal_driver = None
    if enable_terminal:
        terminal_driver = TerminalRuntimeDriver(
            session_manager=terminal_session_manager,
            adapter_factory=terminal_adapter_factory or TerminalNodeAdapter,
            gateway_id=terminal_gateway_id,
            aggregation_ms=terminal_aggregation_ms,
        )
        effective_runtime_drivers.register(RuntimeKind.TERMINAL, terminal_driver)

    remote_driver = None
    if enable_remote:
        remote_driver = RemoteRuntimeDriver()
        effective_runtime_drivers.register(RuntimeKind.REMOTE, remote_driver)

    external_driver = None
    if enable_external:
        external_driver = ExternalRuntimeDriver()
        effective_runtime_drivers.register(RuntimeKind.EXTERNAL, external_driver)

    supervisor = RuntimeSupervisor(effective_runtime_drivers, store=effective_store)
    reconciler = ActorReconciler(supervisor, store=effective_store)
    recovery_service = ActorRecoveryService(
        reconciler,
        limit=recovery_limit,
        strict=recovery_strict,
    )
    api = SystemAPI(
        store=effective_store,
        runtime_supervisor=supervisor,
        reconciler=reconciler,
    )
    gateway = GatewayServer(
        store=effective_store,
        api=api,
    )
    if local_driver is not None:
        local_driver.bind_gateway(gateway)
    if terminal_driver is not None:
        terminal_driver.bind_gateway(gateway)
    if remote_driver is not None:
        remote_driver.bind_gateway(gateway)
    if external_driver is not None:
        external_driver.bind_gateway(gateway)
    gateway.register_managed_service(supervisor)
    if recover_on_start:
        gateway.register_managed_service(recovery_service)
    return EmbeddedRuntimeGatewayStack(
        gateway=gateway,
        runtime_drivers=effective_runtime_drivers,
        supervisor=supervisor,
        reconciler=reconciler,
        recovery_service=recovery_service,
        local_driver=local_driver,
        terminal_driver=terminal_driver,
        remote_driver=remote_driver,
        external_driver=external_driver,
    )
