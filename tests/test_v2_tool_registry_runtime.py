from __future__ import annotations

import json
import logging
from pathlib import Path

import tyrant_core.runtime.tool_registry as tool_registry_module
from tyrant_core.runtime.tool_registry import McpConfig, ToolGroup, ToolRegistry


def test_v2_tool_registry_registers_and_queries_groups() -> None:
    registry = ToolRegistry()
    builtin = ToolGroup(
        name="file_ops",
        description="file tools",
        tool_names=["file_read", "file_grep"],
    )
    mcp = ToolGroup(
        name="github",
        description="github mcp",
        mcp_config=McpConfig(
            command="npx",
            args=["@modelcontextprotocol/server-github"],
            env={"TOKEN": "secret"},
        ),
    )

    registry.register_group(builtin)
    registry.register_group(mcp)

    assert builtin.is_mcp is False
    assert mcp.is_mcp is True
    assert registry.get_group("file_ops") is builtin
    assert registry.list_groups() == [builtin, mcp]
    assert registry.validate_tools(["file_ops", "github", "missing"]) == ["missing"]
    assert registry.get_tool_names(["file_ops", "github", "missing"]) == {
        "file_read",
        "file_grep",
    }
    assert registry.get_mcp_groups(["file_ops", "github", "missing"]) == [mcp]


def test_v2_tool_registry_creates_default_config_when_missing(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config" / "tools.json"
    monkeypatch.setattr(tool_registry_module, "TOOLS_CONFIG_PATH", config_path)

    registry = ToolRegistry()
    tool_registry_module._load_mcp_groups_from_config(registry)

    assert config_path.is_file()
    assert registry.list_groups() == []
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "_example" in data


def test_v2_tool_registry_warns_when_default_config_cannot_be_created(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    config_path = tmp_path / "config" / "tools.json"
    monkeypatch.setattr(tool_registry_module, "TOOLS_CONFIG_PATH", config_path)

    def fail_mkdir(*args, **kwargs):
        raise OSError("mkdir boom")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    caplog.set_level(logging.WARNING)

    tool_registry_module._ensure_default_config()

    assert "无法创建默认 tools.json" in caplog.text


def test_v2_tool_registry_loads_valid_mcp_groups_and_skips_invalid_entries(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "tools.json"
    config_path.write_text(
        json.dumps({
            "_comment": {
                "description": "ignored",
                "command": "npx",
            },
            "github": {
                "description": "github tools",
                "command": "npx",
                "args": ["@modelcontextprotocol/server-github"],
                "env": {"TOKEN": "secret"},
            },
            "broken": {
                "description": "missing command",
            },
            "not_a_group": "invalid",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool_registry_module, "TOOLS_CONFIG_PATH", config_path)

    registry = ToolRegistry()
    tool_registry_module._load_mcp_groups_from_config(registry)
    group = registry.get_group("github")

    assert group is not None
    assert group.description == "github tools"
    assert group.mcp_config == McpConfig(
        command="npx",
        args=["@modelcontextprotocol/server-github"],
        env={"TOKEN": "secret"},
    )
    assert registry.get_group("broken") is None
    assert registry.get_group("not_a_group") is None


def test_v2_tool_registry_ignores_invalid_json_and_non_mapping_config(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "tools.json"
    monkeypatch.setattr(tool_registry_module, "TOOLS_CONFIG_PATH", config_path)

    config_path.write_text("{not-json", encoding="utf-8")
    registry = ToolRegistry()
    tool_registry_module._load_mcp_groups_from_config(registry)
    assert registry.list_groups() == []

    config_path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
    tool_registry_module._load_mcp_groups_from_config(registry)
    assert registry.list_groups() == []


def test_v2_get_tool_registry_builds_singleton_with_builtin_and_mcp(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "tools.json"
    config_path.write_text(
        json.dumps({
            "github": {
                "description": "github tools",
                "command": "npx",
                "args": ["@modelcontextprotocol/server-github"],
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool_registry_module, "TOOLS_CONFIG_PATH", config_path)
    monkeypatch.setattr(tool_registry_module, "_registry", None)

    registry = tool_registry_module.get_tool_registry()

    assert registry.get_group("file_ops") is not None
    assert registry.get_group("terminal") is not None
    assert registry.get_group("github") is not None
    assert registry.get_tool_names(["file_ops", "terminal"]) == {
        "file_read",
        "file_grep",
        "terminal_start",
        "terminal_read",
        "terminal_write",
        "terminal_signal",
        "terminal_close",
    }
    assert tool_registry_module.get_tool_registry() is registry
