# TyrantCore 变更记录

## v1 — 基础设施完整实现

核心实体 + 持久化 + 消息路由 + 事件系统 + Agent 运行时 + CLI。

- models.py: Node, Relation, Message, Task, Event, Reference + 异常体系
- store: Store ABC + SqliteStore（7 表 CRUD + 事务）
- bus: MessageBus（直接关系路由 + inbox）+ EventBus（pub/sub + append-only 日志）
- api: SystemAPI（create_agent / destroy / recruit / release / audit）
- runtime: ADK LlmAgent + Runner + 8 个 tools
- cli: prompt_toolkit + rich，12 个命令
- config: pydantic-settings（TYRANT_* 环境变量）
- 75 tests passed

## v2 — 递归任务分解

驱动场景：Tyrant 说一句话 → 节点自动拆解任务、创建人员、递归分配。

- Task.result 字段：完成者附上执行结果
- Task 完成自动通知：update_task(COMPLETED) → 系统自动给 creator 发 NOTIFICATION Message
- Task 查询 tool：Agent 可查自己创建/分配的 Task，减轻 LLM 记忆负担
- 预设层：启动时自动创建"首席助手"节点 + 递归分解 system prompt
- 81 tests passed

## v3 — 权限重设计 + 委托机制

驱动场景：共享服务型节点需要跨子树操作，bool 权限 + 固定子树作用域无法支持。

- 权限从 bool 变为 Permission 对象列表（action + effect + scope）
- scope 类型：self_subtree / global / subtrees / nodes
- 检查优先级：deny > allow > 默认拒绝
- Delegation 实体：临时/持久授权转移，不可再委托
- grant_permission / revoke_permission / create_delegation / revoke_delegation API
- 94 tests passed

## v3.5 — 消息查询 + 对话历史可观测性

驱动场景：节点收发消息需可查询，Agent 的 LLM 对话历史需可观测。

- ConversationTurn 模型：role 细粒度（user/model/tool_call/tool_result）
- 框架解耦：查询侧走 store，runtime 只负责写入（唯一耦合点）
- query_messages / query_conversation_history API + 权限检查
- Auto-reply：LLM 最终输出自动发回给发送者，带 reply_to reference
- final response 不写 turn（避免与 reply Message 重复）
- CLI: /messages + /history 分组展示
- 120 tests passed

## v4 — 终端能力 + Agent 输入管理

驱动场景：Agent 需要与终端交互，超长输入需自动截断保护。

- TerminalSession：pty 管理（os.openpty + asyncio）
- Terminal tools（范式 1）：terminal_start/read/write/signal/close
- TerminalNode adapter（范式 2）：NodeType.TERMINAL，stdout 聚合 → Message
- File tools：file_read / file_grep
- 截断机制：after_tool_callback + 消息输入截断，超长存文件 + 尾部截取
- 配置前缀迁移 ASA_ → TYRANT_
- 163 tests passed

## v5 — 服务发现 + Agent Creator + 请求级 Session

驱动场景：需要 agentic 的 Agent 创建器，支持服务发现和无状态请求处理。

- Node.tags：服务发现标签 + list_nodes 按 tags 过滤
- session_mode：persistent（默认）/ per_request（每条消息独立 session）
- clone_node：复制 agent_config + permissions + tags 创建新节点
- agent_creator 预设节点：per_request，global 权限，专用 prompt
- 通用 prompt 加 list_nodes 服务发现提示
- 174 tests passed

## 调研记录

- 2026-03-18：新增 [`docs/investigation-20260318.md`](investigation-20260318.md)，记录一次针对仓库结构、核心调用链、已实现能力、风险点与修复优先级的集中调查。
- 2026-03-24：新增 [`docs/architecture-v2.md`](architecture-v2.md)，提出一版不以兼容 v1 为目标的 TyrantCore v2 对象模型与模块设计稿，核心方向是 `Actor / Grant / Thread / Envelope / Delivery / Binding / Session / WorkItem` + 独立 `gateway/` 控制平面。
- 2026-03-24：新增 [`docs/v2-execution-plan.md`](v2-execution-plan.md)，把 v2 重构拆成分阶段执行计划，并已完成 `legacy` 参考区冻结与 v2 gateway/api/bus/runtime 骨架初始化。
- 2026-03-24：活跃命名空间已切到 v2 scaffold：`models.py` 改为 v2 领域对象，`store/api/bus/runtime` 默认实现改为 v2 占位实现，`python -m tyrant_core` 现已走 v2 入口。
- 2026-03-24：完成 Phase 3 的最小闭环：`CommandAPI / QueryAPI` 接入共享 Store，`GatewayServer + DeliveryEngine` 打通 delivery lease/ack，新增 embedded CLI connector 与 local runtime consumer，并通过 [`tests/test_v2_gateway_flow.py`](../tests/test_v2_gateway_flow.py) 验证往返链路。
- 2026-03-24：补完当前范围内的 Phase 4：新增 `tyrant_core.legacy_main` 显式 legacy 入口，`README.md` 默认说明切到 v2，`docs/architecture.md` 标记为 v1/legacy 参考文档，`tests/README.md` 明确旧测试不再作为 v2 默认验收基线。
- 2026-03-25：`src/tyrant_core/store/sqlite_store.py` 已替换为真实的 v2 SQLite 持久化实现，表结构对齐 `Actor / OrgEdge / Grant / Thread / Envelope / Delivery / Binding / Session / Attachment / Presence / WorkItem / DomainEvent`。
- 2026-03-25：`BindingResolver` 与 `SessionRegistry` 已改为 store-backed 实现，`GatewayServer` 启停过程现会统一管理 store lifecycle，CLI 会话 thread 绑定可跨重启复用。
- 2026-03-25：新增 [`tests/test_v2_store.py`](../tests/test_v2_store.py) 与 [`tests/test_v2_gateway_recovery.py`](../tests/test_v2_gateway_recovery.py)，v2 当前默认验收已覆盖持久化、delivery 状态机与 gateway 重启恢复。
- 2026-03-25：`SystemAPI / CommandAPI / QueryAPI` 已补齐 `OrgEdge / Grant / Attachment / Presence / WorkItem / PolicyEngine` 等 v2 核心对象边界，不再只有 `Actor / Thread / Envelope` 可操作。
- 2026-03-25：活跃适配器 [`src/tyrant_core/adapters/cli.py`](../src/tyrant_core/adapters/cli.py) 与 [`src/tyrant_core/adapters/terminal_node.py`](../src/tyrant_core/adapters/terminal_node.py) 已完成 v2 重写，彻底去除对 `Node / MessageBus` 的活跃依赖。
- 2026-03-25：新增 [`tests/test_v2_core_api.py`](../tests/test_v2_core_api.py) 与 [`tests/test_v2_adapters.py`](../tests/test_v2_adapters.py)，v2 默认验收现覆盖核心对象 API、grant/policy 与活跃 adapter。

---

## Roadmap

### 非交互式 CLI（命令行协作接口）
- 子命令模式（`tyrant-core nodes list` 等）
- JSON 输出格式（方便外部系统解析）
- 身份认证（--as <node_id>）
- 后续考虑 HTTP API
