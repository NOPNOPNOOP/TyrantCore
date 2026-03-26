"""ToolRegistry 单元测试 + _build_tools 按配置过滤测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyrant_core.models import Endpoint, Node, NodeStatus, NodeType
import tyrant_core.runtime.tool_registry as tr_mod
from tyrant_core.runtime.tool_registry import (
    McpConfig,
    ToolGroup,
    ToolRegistry,
    _load_mcp_groups_from_config,
    get_tool_registry,
)

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


class TestToolRegistry:
    """测试 ToolRegistry 基础功能。"""

    def test_register_and_get_group(self):
        registry = ToolRegistry()
        group = ToolGroup(
            name="test_group",
            description="测试工具组",
            tool_names=["tool_a", "tool_b"],
        )
        registry.register_group(group)
        result = registry.get_group("test_group")
        assert result is not None
        assert result.name == "test_group"
        assert result.tool_names == ["tool_a", "tool_b"]

    def test_get_nonexistent_group(self):
        registry = ToolRegistry()
        assert registry.get_group("nonexistent") is None

    def test_list_groups(self):
        registry = ToolRegistry()
        registry.register_group(ToolGroup(name="a", description="A", tool_names=["t1"]))
        registry.register_group(ToolGroup(name="b", description="B", tool_names=["t2"]))
        groups = registry.list_groups()
        assert len(groups) == 2
        names = {g.name for g in groups}
        assert names == {"a", "b"}

    def test_validate_tools_all_valid(self):
        registry = ToolRegistry()
        registry.register_group(ToolGroup(name="file_ops", description="", tool_names=[]))
        registry.register_group(ToolGroup(name="terminal", description="", tool_names=[]))
        invalid = registry.validate_tools(["file_ops", "terminal"])
        assert invalid == []

    def test_validate_tools_with_invalid(self):
        registry = ToolRegistry()
        registry.register_group(ToolGroup(name="file_ops", description="", tool_names=[]))
        invalid = registry.validate_tools(["file_ops", "nonexistent", "bad"])
        assert invalid == ["nonexistent", "bad"]

    def test_validate_tools_empty(self):
        registry = ToolRegistry()
        assert registry.validate_tools([]) == []

    def test_get_tool_names(self):
        registry = ToolRegistry()
        registry.register_group(ToolGroup(
            name="file_ops", description="", tool_names=["file_read", "file_grep"],
        ))
        registry.register_group(ToolGroup(
            name="terminal", description="", tool_names=["terminal_start", "terminal_close"],
        ))
        registry.register_group(ToolGroup(
            name="mcp_group", description="", mcp_config=McpConfig(command="echo"),
        ))
        # 单组
        names = registry.get_tool_names(["file_ops"])
        assert names == {"file_read", "file_grep"}
        # 多组
        names = registry.get_tool_names(["file_ops", "terminal"])
        assert names == {"file_read", "file_grep", "terminal_start", "terminal_close"}
        # 空
        assert registry.get_tool_names([]) == set()
        # 不存在的组被忽略
        names = registry.get_tool_names(["file_ops", "nonexistent"])
        assert names == {"file_read", "file_grep"}
        # MCP 组不返回 tool_names
        names = registry.get_tool_names(["mcp_group"])
        assert names == set()

    def test_register_overwrites(self):
        registry = ToolRegistry()
        registry.register_group(ToolGroup(name="g", description="v1", tool_names=["a"]))
        registry.register_group(ToolGroup(name="g", description="v2", tool_names=["b"]))
        g = registry.get_group("g")
        assert g is not None
        assert g.description == "v2"
        assert g.tool_names == ["b"]


class TestGlobalRegistry:
    """测试全局单例。"""

    def setup_method(self):
        """每个测试前重置全局单例。"""
        tr_mod._registry = None

    def teardown_method(self):
        """每个测试后重置全局单例。"""
        tr_mod._registry = None

    @patch.object(tr_mod, "TOOLS_CONFIG_PATH", new_callable=lambda: property(lambda self: MagicMock(is_file=MagicMock(return_value=False))))
    def test_get_tool_registry_has_builtin_groups(self, _):
        """无配置文件时只有内置组。"""
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH") as mock_path:
            mock_path.is_file.return_value = False
            registry = get_tool_registry()
        groups = registry.list_groups()
        names = {g.name for g in groups}
        assert "file_ops" in names
        assert "terminal" in names
        # web_search 不再硬编码，无配置文件时不存在
        assert "web_search" not in names

    def test_file_ops_group_content(self):
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH") as mock_path:
            mock_path.is_file.return_value = False
            registry = get_tool_registry()
        g = registry.get_group("file_ops")
        assert g is not None
        assert "file_read" in g.tool_names
        assert "file_grep" in g.tool_names

    def test_terminal_group_content(self):
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH") as mock_path:
            mock_path.is_file.return_value = False
            registry = get_tool_registry()
        g = registry.get_group("terminal")
        assert g is not None
        assert "terminal_start" in g.tool_names
        assert "terminal_close" in g.tool_names


def _make_node(agent_config: dict | None = None) -> Node:
    """构造测试用 Node。"""
    metadata = {}
    if agent_config is not None:
        metadata["agent_config"] = agent_config
    return Node(
        id="test-node-id",
        type=NodeType.LOCAL_AGENT,
        name="test_agent",
        endpoint=Endpoint(protocol="local", address="test"),
        status=NodeStatus.ONLINE,
        metadata=metadata,
        created_at=NOW,
    )


def _make_runtime():
    """构造最小化的 AgentRuntime（mock 依赖）。"""
    from tyrant_core.runtime.agent_runtime import AgentRuntime

    api = MagicMock()
    message_bus = MagicMock()
    event_bus = MagicMock()
    settings = MagicMock()
    settings.get_litellm_model.return_value = "test-model"
    settings.tool_truncate_enabled = False
    settings.msg_truncate_enabled = False
    settings.terminal_output_aggregation_ms = 100

    runtime = AgentRuntime(
        api=api,
        message_bus=message_bus,
        event_bus=event_bus,
        settings=settings,
    )
    return runtime


# 系统工具名称集合（15 个，所有 Agent 必有）
SYSTEM_TOOL_NAMES = {
    "send_message", "create_task", "update_task", "query_tasks",
    "create_agent", "release_agent", "query_talent_pool", "recruit_agent",
    "create_delegation", "revoke_delegation", "clone_node", "list_nodes",
    "query_messages", "query_conversation_history", "list_tools",
}

FILE_OPS_TOOL_NAMES = {"file_read", "file_grep"}
TERMINAL_TOOL_NAMES = {
    "terminal_start", "terminal_read", "terminal_write",
    "terminal_signal", "terminal_close",
}
def _tool_names(tools) -> set[str]:
    """从 tool 函数列表提取函数名集合（跳过非函数对象如 McpToolset）。"""
    names = set()
    for t in tools:
        if hasattr(t, "__name__"):
            names.add(t.__name__)
    return names


def _has_mcp_toolset(tools) -> bool:
    """检查 tools 列表中是否包含 MCP toolset 对象（非函数）。"""
    return any(not hasattr(t, "__name__") for t in tools)


class TestBuildToolsFiltering:
    """测试 _build_tools 按 agent_config.tools 过滤可选工具。"""

    def test_no_tools_config_only_system(self):
        """无 agent_config.tools → 只有系统工具。"""
        runtime = _make_runtime()
        node = _make_node()
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert SYSTEM_TOOL_NAMES <= names
        assert not (FILE_OPS_TOOL_NAMES & names)
        assert not (TERMINAL_TOOL_NAMES & names)
        assert not _has_mcp_toolset(tools)

    def test_empty_tools_list_only_system(self):
        """agent_config.tools=[] → 只有系统工具。"""
        runtime = _make_runtime()
        node = _make_node(agent_config={"tools": []})
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert SYSTEM_TOOL_NAMES <= names
        assert not (FILE_OPS_TOOL_NAMES & names)
        assert not (TERMINAL_TOOL_NAMES & names)
        assert not _has_mcp_toolset(tools)

    def test_file_ops_only(self):
        """tools=["file_ops"] → 系统工具 + file_ops。"""
        runtime = _make_runtime()
        node = _make_node(agent_config={"tools": ["file_ops"]})
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert SYSTEM_TOOL_NAMES <= names
        assert FILE_OPS_TOOL_NAMES <= names
        assert not (TERMINAL_TOOL_NAMES & names)
        assert not _has_mcp_toolset(tools)

    def test_terminal_only(self):
        """tools=["terminal"] → 系统工具 + terminal。"""
        runtime = _make_runtime()
        node = _make_node(agent_config={"tools": ["terminal"]})
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert SYSTEM_TOOL_NAMES <= names
        assert TERMINAL_TOOL_NAMES <= names
        assert not (FILE_OPS_TOOL_NAMES & names)
        assert not _has_mcp_toolset(tools)

    def test_both_builtin_groups(self):
        """tools=["file_ops", "terminal"] → 系统工具 + file_ops + terminal。"""
        runtime = _make_runtime()
        node = _make_node(agent_config={"tools": ["file_ops", "terminal"]})
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert SYSTEM_TOOL_NAMES <= names
        assert FILE_OPS_TOOL_NAMES <= names
        assert TERMINAL_TOOL_NAMES <= names
        assert not _has_mcp_toolset(tools)

    @patch("tyrant_core.runtime.agent_runtime.AgentRuntime._build_mcp_toolset")
    def test_web_search_only(self, mock_build_mcp):
        """tools=["web_search"] → 系统工具 + MCP toolset。"""
        sentinel = MagicMock(name="McpToolsetMock")
        mock_build_mcp.return_value = sentinel
        runtime = _make_runtime()
        node = _make_node(agent_config={"tools": ["web_search"]})
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert SYSTEM_TOOL_NAMES <= names
        assert not (FILE_OPS_TOOL_NAMES & names)
        assert not (TERMINAL_TOOL_NAMES & names)
        assert sentinel in tools
        mock_build_mcp.assert_called_once()

    @patch("tyrant_core.runtime.agent_runtime.AgentRuntime._build_mcp_toolset")
    def test_all_groups(self, mock_build_mcp):
        """tools=["file_ops", "terminal", "web_search"] → 全部工具。"""
        sentinel = MagicMock(name="McpToolsetMock")
        mock_build_mcp.return_value = sentinel
        runtime = _make_runtime()
        node = _make_node(agent_config={"tools": ["file_ops", "terminal", "web_search"]})
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert SYSTEM_TOOL_NAMES <= names
        assert FILE_OPS_TOOL_NAMES <= names
        assert TERMINAL_TOOL_NAMES <= names
        assert sentinel in tools

    def test_system_tool_count(self):
        """系统工具固定 15 个。"""
        runtime = _make_runtime()
        node = _make_node()
        tools = runtime._build_tools(node)
        names = _tool_names(tools)
        assert len(names) == 15


# ── MCP 配置文件加载测试 ─────────────────────────────────────────────────────

SAMPLE_JSON = """\
{
  "web_search": {
    "description": "网络搜索工具（MCP）",
    "command": "npx",
    "args": ["open-websearch@latest"],
    "env": {
      "MODE": "stdio",
      "DEFAULT_SEARCH_ENGINE": "duckduckgo"
    }
  },
  "another_mcp": {
    "description": "另一个 MCP 工具",
    "command": "uvx",
    "args": ["some-mcp-server"]
  }
}
"""


class TestLoadMcpGroupsFromConfig:
    """测试 _load_mcp_groups_from_config 从 JSON 文件加载 MCP 工具组。"""

    def test_load_valid_json(self, tmp_path):
        """正常 JSON 文件 → 加载所有 MCP 工具组。"""
        json_file = tmp_path / "tools.json"
        json_file.write_text(SAMPLE_JSON, encoding="utf-8")
        registry = ToolRegistry()
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH", json_file):
            _load_mcp_groups_from_config(registry)
        # web_search
        ws = registry.get_group("web_search")
        assert ws is not None
        assert ws.is_mcp
        assert ws.mcp_config.command == "npx"
        assert ws.mcp_config.args == ["open-websearch@latest"]
        assert ws.mcp_config.env == {"MODE": "stdio", "DEFAULT_SEARCH_ENGINE": "duckduckgo"}
        # another_mcp
        am = registry.get_group("another_mcp")
        assert am is not None
        assert am.is_mcp
        assert am.mcp_config.command == "uvx"

    def test_file_not_exist_creates_default(self, tmp_path):
        """文件不存在 → 自动生成默认配置，无 MCP 组注册。"""
        json_file = tmp_path / "tools.json"
        registry = ToolRegistry()
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH", json_file):
            _load_mcp_groups_from_config(registry)
        # 无实际工具组注册（_example 被跳过）
        assert registry.list_groups() == []
        # 默认配置文件已生成
        assert json_file.is_file()
        data = json.loads(json_file.read_text(encoding="utf-8"))
        assert "_example" in data

    def test_invalid_json_silent(self, tmp_path):
        """JSON 解析失败 → 静默跳过，不报错。"""
        json_file = tmp_path / "tools.json"
        json_file.write_text("{{invalid json", encoding="utf-8")
        registry = ToolRegistry()
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH", json_file):
            _load_mcp_groups_from_config(registry)
        assert registry.list_groups() == []

    def test_missing_command_skipped(self, tmp_path):
        """缺少 command 字段 → 跳过该组。"""
        json_file = tmp_path / "tools.json"
        json_file.write_text('{"bad": {"description": "no command"}}', encoding="utf-8")
        registry = ToolRegistry()
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH", json_file):
            _load_mcp_groups_from_config(registry)
        assert registry.get_group("bad") is None

    def test_get_tool_registry_with_config(self, tmp_path):
        """get_tool_registry() 集成测试：内置组 + 配置文件 MCP 组。"""
        json_file = tmp_path / "tools.json"
        json_file.write_text(SAMPLE_JSON, encoding="utf-8")
        tr_mod._registry = None
        with patch.object(tr_mod, "TOOLS_CONFIG_PATH", json_file):
            registry = get_tool_registry()
        names = {g.name for g in registry.list_groups()}
        assert "file_ops" in names
        assert "terminal" in names
        assert "web_search" in names
        assert "another_mcp" in names
        # 清理
        tr_mod._registry = None
