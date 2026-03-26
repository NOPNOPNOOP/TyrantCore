# adapters/terminal.py + terminal_node.py — 终端子系统

> 注意：本文描述的是 v1 / legacy 终端子系统文档，当前活跃实现请优先阅读 [`docs/architecture-v2.md`](../architecture-v2.md) 与 [`docs/v2-execution-plan.md`](../v2-execution-plan.md)。

依赖：models, api, message_bus

## 两种终端范式

| 范式 | 模块 | 说明 |
|------|------|------|
| Agent 作为操作者 | terminal_tools.py | Agent 通过 tool call 主动管理终端会话 |
| 终端作为 Node | terminal_node.py | 终端进程包装为 TERMINAL 节点，接入通信网络 |

## TerminalSession (terminal.py)

基于 pty 的交互式进程管理：
- `start()` — openpty + create_subprocess_exec，后台 _read_loop 持续读取到 buffer
- `read(timeout)` — 返回 buffer 内容，无内容则等待
- `write(data)` — 写入 stdin
- `send_signal(sig)` — 向子进程发信号
- `is_alive()` / `idle_seconds()` / `close()`

## TerminalSessionManager (terminal.py)

管理所有 TerminalSession：
- `create_session(command, cwd, env, owner_agent_id) → session_id`
- `get_session(session_id)` / `close_session(session_id)`
- `close_sessions_for_agent(agent_id)` — Agent 停止时清理
- `cleanup_idle(max_idle_seconds)` — 清理超时会话
- `close_all()`

## TerminalNodeAdapter (terminal_node.py)

将终端进程包装为 TyrantCore Node（范式 2）：
- stdout 按时间窗口聚合 → TERMINAL_OUTPUT Message 发给 parent
- 收到 Message → 提取 text 写入 stdin
- 进程退出 → 发送 TERMINAL_EXIT Message
- 配置：`TYRANT_TERMINAL_OUTPUT_AGGREGATION_MS`（聚合窗口，默认 500ms）
