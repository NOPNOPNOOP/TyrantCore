# runtime/ — Agent 运行时

> 注意：本文描述的是 v1 / legacy runtime 结构，当前活跃实现请优先阅读 [`docs/architecture-v2.md`](../architecture-v2.md) 与 [`docs/v2-execution-plan.md`](../v2-execution-plan.md)。

依赖：api, message_bus, event_bus, models, config, adk, terminal, file_tools, terminal_tools, tool_registry, truncation, mcp

## 职责

- 管理 LOCAL_AGENT 和 TERMINAL 节点的生命周期（启动/停止/恢复/关闭）
- ADK LlmAgent 创建 + Runner.run_async() 消息处理循环
- 将 SystemAPI 暴露为 ADK tools
- 工具池管理：系统工具（必有）+ 可选工具组（按需配备）
- ConversationTurn 持久化（唯一框架耦合点）
- 自动回复机制（response_text → reply Message with reply_to reference）

## AgentRuntime 接口

- `start_agent(node)` — 根据 NodeType dispatch：LOCAL_AGENT → ADK loop，TERMINAL → TerminalNodeAdapter
- `stop_agent(node_id)` — 取消协程 + 清理终端会话
- `recover()` — 从 store 恢复所有 ONLINE 的 LOCAL_AGENT/TERMINAL
- `shutdown()` — 优雅关闭所有 Agent 和终端会话

## Session 模式

- `persistent`（默认）：启动时创建一次 ADK session，所有消息共享上下文
- `per_request`：每条消息新建临时 session，处理完丢弃，适合无状态服务节点

通过 `node.metadata.agent_config.session_mode` 配置。

## 工具池（Tool Registry）

工具分为系统工具和可选工具组两类：

### 系统工具（15 个，所有 Agent 必有）

| 分类 | Tools |
|------|-------|
| 消息 | send_message, query_messages, query_conversation_history |
| 任务 | create_task, update_task, query_tasks |
| 组织 | create_agent, release_agent, recruit_agent, query_talent_pool |
| 权限 | create_delegation, revoke_delegation |
| 服务发现 | list_nodes, clone_node |
| 工具池 | list_tools |

### 可选工具组（按需配备）

通过 `agent_config.tools: list[str]` 指定启用的工具组。不指定则无可选工具。

| 工具组 | 包含工具 | 适用场景 |
|--------|----------|----------|
| `file_ops` | file_read, file_grep | 需要读取/搜索文件的 Agent |
| `terminal` | terminal_start, terminal_read, terminal_write, terminal_signal, terminal_close | 需要执行命令的 Agent |
| `web_search` | （MCP）open-websearch | 需要搜索互联网或抓取网页的 Agent |

### 配置示例

```python
# 创建带文件和终端工具的 Agent
await api.create_agent(
    caller_id=caller_id,
    name="开发助手",
    parent_id=parent_id,
    agent_config={
        "instruction": "...",
        "tools": ["file_ops", "terminal"],
    },
)

# 创建纯协调类 Agent（无可选工具）
await api.create_agent(
    caller_id=caller_id,
    name="项目经理",
    parent_id=parent_id,
    agent_config={"instruction": "..."},
)
```

### create_agent tool 的 tools 参数

Agent 通过 `create_agent` tool 创建下属时，可传 `tools` 参数指定可选工具组。
用 `list_tools` tool 查看所有可用工具组。

## 子模块

### tool_registry.py

工具池注册表，管理可选工具组（内置 + MCP）：
- `McpConfig` dataclass：command, args, env（MCP server 连接配置）
- `ToolGroup` dataclass：name, description, tool_names（内置）或 mcp_config（MCP）
- `ToolRegistry` 类：register_group, get_group, list_groups, validate_tools, get_tool_names, get_mcp_groups
- `get_tool_registry()` 全局单例，内置 file_ops + terminal 两个内置组，MCP 工具组从外部配置文件加载
- `_load_mcp_groups_from_config(registry)` 从 `~/.config/tyrant_core/tools.json` 加载 MCP 工具组

### file_tools.py

文件操作工具组（可选，需配备 `file_ops`）：
- `file_read(path, start_line, end_line)` — 按行范围读取，带行号前缀
- `file_grep(path, pattern, max_matches)` — 正则搜索匹配行

### terminal_tools.py

终端操作工具组（可选，需配备 `terminal`）：
- `terminal_start(command, cwd, env)` — 创建 pty session
- `terminal_read(session_id, timeout)` — 读取输出
- `terminal_write(session_id, input)` — 写入 stdin
- `terminal_signal(session_id, signal_name)` — 发送信号
- `terminal_close(session_id)` — 关闭会话

### MCP 工具组

通过 MCP（Model Context Protocol）接入外部工具服务器，由 ADK `McpToolset` + `StdioConnectionParams` 桥接。

#### 外部配置文件

MCP 工具组通过 `~/.config/tyrant_core/tools.json` 配置，不再硬编码。文件不存在时静默跳过（无 MCP 工具组可用）。

配置格式：

```json
{
  "web_search": {
    "description": "网络搜索工具（MCP）：多引擎搜索互联网、抓取网页内容",
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
```

每个顶层 key = 一个 MCP 工具组：
- `description`（必填）：工具组描述，Agent 通过 `list_tools` 可见
- `command`（必填）：启动命令（如 `npx`、`uvx`）
- `args`（可选）：命令参数列表
- `env`（可选）：环境变量对象

#### 内置 MCP 工具组推荐

- `web_search`：基于 [open-websearch](https://github.com/Aas-ee/open-webSearch)，多引擎搜索 + 网页抓取，免费无需 API Key

### truncation.py

保护 Agent 上下文窗口：
- `truncate_if_needed(content, ...)` — 超出阈值时存文件 + 返回尾部截取 + file_read 指引
- 配置：`TYRANT_TOOL_TRUNCATE_*`（tool 返回值）、`TYRANT_MSG_TRUNCATE_*`（消息输入）
