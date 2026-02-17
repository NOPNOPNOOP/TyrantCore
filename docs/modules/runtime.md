# runtime/ — Agent 运行时

依赖：api, message_bus, event_bus, models, config, adk, terminal, file_tools, terminal_tools, truncation

## 职责

- 管理 LOCAL_AGENT 和 TERMINAL 节点的生命周期（启动/停止/恢复/关闭）
- ADK LlmAgent 创建 + Runner.run_async() 消息处理循环
- 将 SystemAPI 暴露为 ADK tools
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

## Tools 列表

AgentRuntime._build_tools(agent_id) 为每个 Agent 构建：

| 分类 | Tools |
|------|-------|
| 消息 | send_message, query_messages, query_conversation_history |
| 任务 | create_task, update_task, query_tasks |
| 组织 | create_agent, release_agent, recruit_agent, query_talent_pool |
| 权限 | create_delegation, revoke_delegation |
| 服务发现 | list_nodes, clone_node |
| 文件 | file_read, file_grep |
| 终端 | terminal_start, terminal_read, terminal_write, terminal_signal, terminal_close |

## 子模块

### file_tools.py

通用文件操作，所有 Agent 可用：
- `file_read(path, start_line, end_line)` — 按行范围读取，带行号前缀
- `file_grep(path, pattern, max_matches)` — 正则搜索匹配行

### terminal_tools.py

Agent 主动管理终端会话（范式 1：Agent 作为操作者）：
- `terminal_start(command, cwd, env)` — 创建 pty session
- `terminal_read(session_id, timeout)` — 读取输出
- `terminal_write(session_id, input)` — 写入 stdin
- `terminal_signal(session_id, signal_name)` — 发送信号
- `terminal_close(session_id)` — 关闭会话

### truncation.py

保护 Agent 上下文窗口：
- `truncate_if_needed(content, ...)` — 超出阈值时存文件 + 返回尾部截取 + file_read 指引
- 配置：`TYRANT_TOOL_TRUNCATE_*`（tool 返回值）、`TYRANT_MSG_TRUNCATE_*`（消息输入）
