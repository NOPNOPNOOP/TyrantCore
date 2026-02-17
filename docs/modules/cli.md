# adapters/cli.py — CLI 适配器

依赖：api, message_bus, models, runtime, prompt_toolkit, rich

## 职责

- Tyrant（人类）的终端交互入口
- 启动时自动创建预设节点（Tyrant 自身 + 首席助手 + agent_creator）
- 命令解析 + 消息收发 + Rich 格式化输出

## 预设节点

启动时按顺序确保存在：

1. **Tyrant** — HUMAN, is_owner=True, endpoint=cli
2. **首席助手** — LOCAL_AGENT, Tyrant 的第一个下属，自动设为对话目标
3. **agent_creator** — LOCAL_AGENT, per_request, tags=["agent_creator"], 拥有 global create_agent + grant_permission 权限

## 命令列表

| 命令 | 说明 |
|------|------|
| `/talk <node> <text>` | 向指定节点发送 CHAT |
| `/target <node>` | 设置默认对话目标 |
| `/inbox` | 查看收件箱 |
| `/nodes` | 列出所有节点 |
| `/relations` | 列出所有关系 |
| `/tasks` | 列出所有任务 |
| `/create <name> <instruction>` | 创建 Agent |
| `/grant <node> <action>` | 授予权限（self_subtree） |
| `/messages [node]` | 查看节点消息 |
| `/history <node>` | 查看 LLM 对话历史（Tree 视图） |
| `/inspect <node>` | 审计查看节点状态 |
| `/events [hours]` | 查看事件日志 |

节点参数支持名称或 ID 前缀解析。

## 消息接收

并行运行 `_input_loop` + `_receive_loop`，收到消息用 Rich Panel 实时显示。
