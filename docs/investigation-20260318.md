# TyrantCore 调查记录（2026-03-18）

本文记录 2026-03-18 对 TyrantCore 仓库的一次集中调查结果，目标是帮助后续维护者快速理解当前实现、验证范围与已确认风险。

---

## 调查范围与方法

本次调查覆盖以下内容：

- 架构与模块文档：`docs/architecture.md`、`docs/modules/`
- 启动与核心实现：`src/tyrant_core/__main__.py`、`api/system_api.py`、`runtime/agent_runtime.py`、`bus/message_bus.py`
- 关键测试：`tests/test_message_bus.py`、`tests/test_system_api.py`、`tests/test_e2e_query.py`、`tests/test_v5_e2e.py`

本次调查实际执行了以下验证：

- 运行测试：
  - `uv run pytest tests/test_message_bus.py tests/test_system_api.py tests/test_e2e_query.py tests/test_v5_e2e.py -q`
  - 结果：`59 passed in 3.32s`
- 额外最小复现：
  - 验证 `ack` 后消息仍可被 `get_inbox()` 取回
  - 验证系统重启后 `runtime.recover()` 未恢复未处理 inbox 消息
  - 验证 `grant_permission()` 存在作用域放大问题

> 本文结论对应的是 2026-03-18 当天仓库状态，不保证后续提交仍然完全一致。

---

## 代码库导览

### 分层结构

TyrantCore 当前实现可以理解为 6 层：

1. **模型层** — `src/tyrant_core/models.py`
   - 定义 `Node`、`Relation`、`Message`、`Task`、`Event`、`Delegation`、`ConversationTurn`
   - 是全系统的语义底座

2. **持久化层** — `src/tyrant_core/store/`
   - `base.py` 定义 Store 契约
   - `sqlite_store.py` 提供 SQLite + SQLAlchemy async 实现

3. **总线层** — `src/tyrant_core/bus/`
   - `message_bus.py`：消息路由、投递、ACK、内存 inbox
   - `event_bus.py`：事件发布与订阅

4. **规则层** — `src/tyrant_core/api/system_api.py`
   - 所有对外业务动作的唯一入口
   - 节点生命周期、权限、委托、任务、查询、审计都从这里进入

5. **运行时层** — `src/tyrant_core/runtime/`
   - `agent_runtime.py`：ADK 集成、消息消费、对话 turn 持久化、自动回复
   - `tool_registry.py`：工具组注册
   - `file_tools.py` / `terminal_tools.py` / `truncation.py`：辅助能力

6. **接入层** — `src/tyrant_core/adapters/`
   - `cli.py`：Tyrant 终端入口
   - `terminal.py` / `terminal_node.py`：终端会话与终端节点适配

### 启动主链路

启动入口在 `src/tyrant_core/__main__.py`，当前装配顺序为：

```text
Settings.apply_env()
  -> SqliteStore.initialize()
  -> EventBus / MessageBus
  -> SystemAPI
  -> AgentRuntime.recover()
  -> CliAdapter.run()
```

### 一条典型消息的处理路径

```text
CLI 输入
  -> SystemAPI.send_message()
  -> MessageBus.send()
  -> MessageBus._deliver()
  -> MessageBus.subscribe_inbox()
  -> AgentRuntime._agent_loop()
  -> Runner.run_async()
  -> AgentRuntime._extract_and_save_turns()
  -> AgentRuntime 自动发送 reply Message
```

### 设计上最重要的语义

- `Message.from_node` 由系统在 `MessageBus.send()` 内覆写，调用者不可伪造
- Agent 的最终文本回复默认不会保存为 `ConversationTurn(role="model")`
- 最终文本会被 runtime 自动回发为 reply message，并带 `reply_to` 引用
- `send_message` tool 的用途是“主动联系其他节点”，不是“回复当前消息”

---

## 已验证的当前能力

以下能力已经被实现，并且在本次调查中通过测试或代码复核确认：

### 1. 组织结构与节点管理

- 支持创建、销毁本地 Agent
- 创建节点时自动建立 `REPORTS_TO`
- 同一上级下的节点会自动建立 `CONNECTED`
- 支持释放到人才池（`SUSPENDED`）与重新招募
- 支持按模板克隆节点，复制 `agent_config`、`permissions`、`tags`

对应测试：

- `tests/test_system_api.py`
- `tests/test_v5_e2e.py`

### 2. 权限与委托

- 权限模型支持 `allow` / `deny`
- 作用域支持 `self_subtree` / `global` / `subtrees` / `nodes`
- 权限判断遵循 `deny > allow > 默认拒绝`
- 支持 delegation
- 通过 delegation 获得的权限不能继续再委托

对应测试：

- `tests/test_system_api.py`

### 3. 消息、审计与可观测性

- 支持点对点和多播消息发送
- 路由模型基于直接关系与祖先/后代关系
- 支持查询消息
- 支持查询 Agent 的 `ConversationTurn`
- 直接上级可审计下属
- 有 `audit` 权限时可跨节点查询

对应测试：

- `tests/test_message_bus.py`
- `tests/test_e2e_query.py`
- `tests/test_system_api.py`

### 4. 任务模型

- 支持创建、更新、查询任务
- `Task.result` 会持久化
- 任务完成或失败时，系统自动向 creator 发送 `NOTIFICATION`
- 任务创建时，如果 assignee 不是 creator，会自动通知 assignee

对应测试：

- `tests/test_system_api.py`

### 5. 运行时行为

- Agent 收到消息后会写入 user turn
- 中间 model 文本会写入 turn
- tool call 与 tool result 会分别写入 turn
- final response 不写入 turn，而是作为 reply message 自动发回
- 支持 `persistent` 与 `per_request` 两种 session 模式

对应测试：

- `tests/test_e2e_query.py`
- `tests/test_v5_e2e.py`

---

## 调查中确认的风险与问题

以下问题中，前 3 项已经通过最小复现确认，不只是静态阅读推测。

### 1. 高优先级：`grant_permission()` 存在作用域放大问题

相关代码：

- `src/tyrant_core/api/system_api.py` 中的 `grant_permission()`
- `src/tyrant_core/api/system_api.py` 中的 `_check_permission()`
- `src/tyrant_core/api/system_api.py` 中的 `_scope_matches()`

现象：

- `grant_permission()` 会检查调用者是否拥有 `grant_permission` 权限
- 也会检查调用者是否拥有被授予的 `action`
- 但第二次检查没有传入目标或期望作用域
- `_scope_matches()` 在 `target_id is None` 时直接返回 `True`

这意味着：

- 调用者只要“拥有某 action”，就可能把这个 action 以更宽的 scope 授给别人
- 例如：仅拥有 `self_subtree create_agent` 的节点，可以向别人授予 `global create_agent`

影响：

- 这是权限模型层面的放大漏洞
- 会绕过“最小权限原则”

建议修复：

- 为 `grant_permission()` 增加“被授予 scope 必须是调用者有效 scope 的子集”校验
- 不要只检查 action 是否存在，还要检查作用域兼容性
- 为此补充专门的回归测试

### 2. 高优先级：重启后未处理消息不会被 runtime 恢复

相关代码：

- `src/tyrant_core/__main__.py`
- `src/tyrant_core/runtime/agent_runtime.py`
- `src/tyrant_core/bus/message_bus.py`

现象：

- `MessageBus` 提供了 `recover_inboxes()`
- 但启动路径只调用了 `AgentRuntime.recover()`
- `AgentRuntime.recover()` 只恢复 ONLINE 的节点，并未恢复内存 inbox

最小复现结论：

- 消息已落库但未被旧进程消费时
- 创建新的 `MessageBus` / `AgentRuntime` 并调用 `runtime.recover()`
- 该消息不会自动进入新进程的 `subscribe_inbox()` 消费流

影响：

- 系统重启后可能丢失“尚未进入运行时处理”的消息
- 当前的“持久化 + 恢复”承诺没有真正闭环

建议修复：

- 在启动恢复流程中显式调用 `message_bus.recover_inboxes(node_ids)`
- 或者重构 `subscribe_inbox()`，让它能够在首次订阅时补拉待处理消息

### 3. 高优先级：`get_inbox()` / `ack()` / `recover_inboxes()` 的语义不一致

相关代码：

- `src/tyrant_core/store/sqlite_store.py` 中的 `get_inbox()`
- `src/tyrant_core/bus/message_bus.py` 中的 `ack()`
- `src/tyrant_core/bus/message_bus.py` 中的 `recover_inboxes()`

现象：

- `ack()` 会写入 deliveries 表
- 但 `get_inbox()` 查询消息时只按 `to_node` 和优先级排序，不过滤 `acked_at`
- 因而消息即使已经 ACK，`get_inbox()` 仍会返回

最小复现结论：

- 一条 `requires_ack=True` 的消息被 `ack()` 后
- 再次调用 `store.get_inbox()` 仍能取回该消息

影响：

- 如果 inbox 的语义是“待处理消息”，当前实现不正确
- 如果未来接上 `recover_inboxes()`，ACK 过的消息也会被重复塞回内存队列

建议修复：

- 先统一语义：`inbox` 到底表示“收件箱”还是“待处理队列”
- 如果表示待处理队列，应在 `get_inbox()` 中关联 deliveries 并过滤已 ACK 项
- 如果表示收件箱，则应重命名接口，避免与恢复逻辑混淆

### 4. 中优先级：缺少 `LlmAgent.name` 的显式合法性校验

相关代码：

- `docs/architecture.md`
- `src/tyrant_core/api/system_api.py`
- `src/tyrant_core/runtime/agent_runtime.py`

现象：

- 文档明确说明 `Node.name` 需要满足 ADK 合法标识符要求
- 但 `create_agent()` 未做校验
- runtime 又直接把 `node.name` 传给 `LlmAgent(name=...)`

影响：

- 一旦用户输入带空格或特殊字符的名字，可能在运行时才失败
- 错误暴露位置会偏晚，不利于排查

建议修复：

- 在 `SystemAPI.create_agent()` 和 `clone_node()` 处统一校验
- 或者在模型层提供合法化/规范化逻辑

### 5. 中低优先级：默认助手的 `web_search` 声明与实际可用工具可能不一致

相关代码：

- `src/tyrant_core/adapters/cli.py`
- `src/tyrant_core/runtime/tool_registry.py`

现象：

- CLI 创建默认助手时会声明 `["file_ops", "terminal", "web_search"]`
- 但 `web_search` 当前不再硬编码内置，而是依赖 `~/.config/tyrant_core/tools.json`
- 没有外部配置时，该工具组不会真正出现在 runtime registry 中

影响：

- 节点元数据与真实可用能力可能不一致
- Agent 可能“以为自己有工具”，运行时却拿不到

建议修复：

- 创建默认助手前先校验工具组是否存在
- 或在 CLI 中根据 registry 动态裁剪默认工具组

---

## 推荐阅读顺序

如果后续要继续改代码，建议按以下顺序进入：

1. `docs/architecture.md`
2. `src/tyrant_core/__main__.py`
3. `src/tyrant_core/api/system_api.py`
4. `src/tyrant_core/runtime/agent_runtime.py`
5. `src/tyrant_core/bus/message_bus.py`
6. `tests/test_e2e_query.py`
7. `tests/test_v5_e2e.py`

---

## 关于 OpenClaw Gateway 的架构取舍

本次调查后补充一个明确结论：

- TyrantCore **可以借鉴** OpenClaw Gateway 的设计思路
- 但 TyrantCore **没有必要** 与 OpenClaw Gateway 建立直接通信或运行时依赖

### 可以借鉴的内容

- Gateway 作为常驻控制平面的职责划分
- 外部渠道接入与内部系统语义的隔离方式
- 会话管理、工具调用、安全边界等设计思路

### 不建议直接引入的内容

- 对 OpenClaw Gateway 运行时的直接依赖
- 对其内部对象模型、协议细节和部署方式的直接绑定
- 为了“兼容某个现成 Gateway”而反向扭曲 TyrantCore 自身的消息、权限和任务模型

### 对 TyrantCore 更合适的做法

- 保持 TyrantCore 为 **gateway-agnostic** 的基础设施内核
- 先把内部的 `Node / Relation / Message / Task / Event / 权限 / runtime` 语义做稳
- 如果未来确实要接外部渠道，应由 TyrantCore 自己定义 adapter 或 gateway 边界
- 届时可以实现一个“受 OpenClaw Gateway 启发”的接入层，但不要求系统必须直连 OpenClaw Gateway

换句话说，OpenClaw Gateway 更适合作为 **参考实现**，而不是 TyrantCore 的 **上游依赖组件**。

---

## 推荐后续动作

建议按优先级推进：

1. 修复 `grant_permission()` 的作用域放大问题，并补回归测试
2. 明确 inbox 语义，统一 `get_inbox()` / `ack()` / `recover_inboxes()` 行为
3. 打通启动恢复路径中的 inbox 恢复
4. 为 `Node.name` 增加 ADK 兼容性校验
5. 统一默认助手工具声明与实际 registry 配置

---

## 附：本次调查可直接作为后续工作的基线

如果后续要修复上述问题，建议优先从以下测试入口补起：

- 权限相关：`tests/test_system_api.py`
- 消息恢复与 ACK 语义：建议新增 `tests/test_message_bus.py` 覆盖
- 运行时恢复：建议新增 `tests/test_e2e_query.py` 或新的 runtime 恢复测试
