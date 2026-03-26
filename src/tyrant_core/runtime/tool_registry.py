"""工具池注册表。管理可选工具组的注册、查询和校验。

支持两种工具组：
- 内置工具组：tool_names 列出函数名，由 agent_runtime 构建
- MCP 工具组：mcp_config 指定 MCP server 连接参数，由 ADK McpToolset 接入
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class McpConfig:
    """MCP server 连接配置。"""
    command: str  # 启动命令，如 "npx"
    args: list[str] = field(default_factory=list)  # 命令参数
    env: dict[str, str] = field(default_factory=dict)  # 环境变量


@dataclass
class ToolGroup:
    """一组可选工具的描述。

    内置工具组：设置 tool_names
    MCP 工具组：设置 mcp_config
    """
    name: str  # 工具组名称
    description: str  # 工具组描述
    tool_names: list[str] = field(default_factory=list)  # 内置工具函数名
    mcp_config: McpConfig | None = None  # MCP 连接配置

    @property
    def is_mcp(self) -> bool:
        return self.mcp_config is not None


class ToolRegistry:
    """工具池注册表。管理可选工具组。"""

    def __init__(self) -> None:
        self._groups: dict[str, ToolGroup] = {}

    def register_group(self, group: ToolGroup) -> None:
        """注册一个工具组。"""
        self._groups[group.name] = group

    def get_group(self, name: str) -> ToolGroup | None:
        """按名称获取工具组。"""
        return self._groups.get(name)

    def list_groups(self) -> list[ToolGroup]:
        """列出所有已注册的工具组。"""
        return list(self._groups.values())

    def validate_tools(self, tool_names: list[str]) -> list[str]:
        """校验工具组名称列表，返回无效的名称。"""
        return [name for name in tool_names if name not in self._groups]

    def get_tool_names(self, group_names: list[str]) -> set[str]:
        """根据工具组名称列表，返回所有包含的 tool 函数名集合（仅内置工具组）。"""
        result: set[str] = set()
        for name in group_names:
            group = self._groups.get(name)
            if group and not group.is_mcp:
                result.update(group.tool_names)
        return result

    def get_mcp_groups(self, group_names: list[str]) -> list[ToolGroup]:
        """根据工具组名称列表，返回其中的 MCP 工具组。"""
        result: list[ToolGroup] = []
        for name in group_names:
            group = self._groups.get(name)
            if group and group.is_mcp:
                result.append(group)
        return result


# ── 外部配置加载 ────────────────────────────────────────────────────────────

_logger = logging.getLogger(__name__)

# MCP 工具组配置文件路径
TOOLS_CONFIG_PATH = Path.home() / ".config" / "tyrant_core" / "tools.json"

# 默认配置：空对象 + 示例注释
_DEFAULT_CONFIG = """\
{
  "_example": {
    "description": "示例 MCP 工具组（以 _ 开头的 key 会被忽略）",
    "command": "npx",
    "args": ["some-mcp-server@latest"],
    "env": {"KEY": "value"}
  }
}
"""


def _ensure_default_config() -> None:
    """文件不存在时自动生成默认空配置。"""
    try:
        TOOLS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOOLS_CONFIG_PATH.write_text(_DEFAULT_CONFIG, encoding="utf-8")
    except Exception as exc:
        _logger.warning("无法创建默认 tools.json: %s", exc)


def _load_mcp_groups_from_config(registry: ToolRegistry) -> None:
    """从 tools.json 加载 MCP 工具组。文件不存在时自动生成默认空配置。"""
    if not TOOLS_CONFIG_PATH.is_file():
        _ensure_default_config()
        return
    try:
        data = json.loads(TOOLS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        _logger.warning("tools.json 解析失败，跳过: %s", exc)
        return
    if not isinstance(data, dict):
        _logger.warning("tools.json 顶层必须是对象，跳过")
        return
    for name, cfg in data.items():
        if name.startswith("_"):
            continue  # 跳过注释字段
        if not isinstance(cfg, dict):
            _logger.warning("tools.json: '%s' 不是有效的工具组配置，跳过", name)
            continue
        description = cfg.get("description", "")
        command = cfg.get("command")
        if not command:
            _logger.warning("tools.json: '%s' 缺少 command 字段，跳过", name)
            continue
        args = cfg.get("args", [])
        env = cfg.get("env", {})
        registry.register_group(ToolGroup(
            name=name,
            description=description,
            mcp_config=McpConfig(command=command, args=args, env=env),
        ))


# ── 全局单例 ──────────────────────────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取全局 ToolRegistry 单例，首次调用时注册内置工具组 + 加载外部 MCP 配置。"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        # 内置工具组（有对应 Python 实现）
        _registry.register_group(ToolGroup(
            name="file_ops",
            description="文件操作工具：读取文件内容、正则搜索文件",
            tool_names=["file_read", "file_grep"],
        ))
        _registry.register_group(ToolGroup(
            name="terminal",
            description="终端操作工具：启动/读取/写入/信号/关闭终端会话",
            tool_names=[
                "terminal_start", "terminal_read", "terminal_write",
                "terminal_signal", "terminal_close",
            ],
        ))
        # 从 tools.json 加载 MCP 工具组
        _load_mcp_groups_from_config(_registry)
    return _registry
