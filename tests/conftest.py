"""测试分层与 marker 自动打标。"""

from __future__ import annotations

from pathlib import Path

import pytest


V2_ACCEPTANCE_FILES = {
    "test_v2_gateway_flow.py",
    "test_v2_gateway_recovery.py",
    "test_v2_core_api.py",
    "test_v2_adapters.py",
    "test_v2_smoke.py",
}

V2_UNIT_FILES = {
    "test_v2_store.py",
    "test_v2_gateway_layers.py",
    "test_v2_cli_components.py",
    "test_v2_terminal_components.py",
    "test_v2_terminal_adapter_layers.py",
    "test_v2_bus_components.py",
    "test_v2_entrypoint.py",
    "test_v2_terminal_connector.py",
    "test_v2_runtime_components.py",
    "test_v2_runtime_tools.py",
    "test_v2_tool_registry_runtime.py",
    "test_v2_session_registry.py",
    "test_v2_query_api.py",
    "test_v2_config.py",
    "test_v2_policies.py",
    "test_v2_cli_edges.py",
    "test_v2_gateway_facades.py",
    "test_v2_remaining_components.py",
    "test_v2_upper_facades.py",
    "test_v2_sqlite_store_edges.py",
}

EXPERIMENTAL_FILES = {
    "test_v5_e2e.py",
}

KNOWN_MARKERS = {
    "v2_acceptance",
    "v2_unit",
    "legacy",
    "experimental",
}


def _marker_for_file(path: Path) -> str:
    name = path.name
    if name in V2_ACCEPTANCE_FILES:
        return "v2_acceptance"
    if name in V2_UNIT_FILES:
        return "v2_unit"
    if name in EXPERIMENTAL_FILES:
        return "experimental"
    return "legacy"


def _selected_markers(markexpr: str) -> set[str]:
    return {marker for marker in KNOWN_MARKERS if marker in markexpr}


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """按 -m 预裁剪测试文件，避免无关测试在导入阶段就打断当前 lane。"""
    path = Path(str(collection_path))
    if path.suffix != ".py" or not path.name.startswith("test_"):
        return None

    selected = _selected_markers(config.option.markexpr or "")
    if not selected:
        return None

    file_marker = _marker_for_file(path)
    return file_marker not in selected


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """按测试文件统一打标，避免在文件里重复维护 marker。"""
    for item in items:
        marker = _marker_for_file(Path(str(item.fspath)))
        item.add_marker(getattr(pytest.mark, marker))
