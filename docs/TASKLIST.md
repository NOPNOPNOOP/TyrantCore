# TyrantCore 实现任务追踪

实现顺序遵循模块依赖图：models → store → bus → api → runtime → cli

## 状态说明

- ⬜ 未开始
- 🔧 进行中
- ✅ 已完成
- 🧪 已完成 + 测试通过

## 任务列表

### 1. 项目脚手架
- ✅ pyproject.toml 配置（依赖、构建、pytest）
- ✅ src/tyrant_core/ 目录结构 + __init__.py
- ✅ tests/ 目录

### 2. models.py — [spec 01](specs/01-models.md)
- ✅ Enums: NodeType, NodeStatus, RelationType, TaskStatus, Priority, RefType
- ✅ 嵌套结构: Endpoint, Permissions, Reference
- ✅ 核心实体: Node, Relation, Message, Task, Event
- ✅ 异常: TyrantError 及子类
- 🧪 测试: tests/test_models.py (20 passed)

### 3. store/ — [spec 02](specs/02-store.md)
- ✅ base.py: Store ABC 抽象接口
- ✅ sqlite_store.py: 7 个 Core Table 定义
- ✅ sqlite_store.py: SqliteStore 完整 CRUD 实现
- ✅ sqlite_store.py: 事务管理 (transaction context manager)
- 🧪 测试: tests/test_store.py (22 passed)

### 4. bus/event_bus.py — [spec 04](specs/04-event-bus.md)
- ✅ 内存订阅注册表 (dict pattern → callbacks)
- ✅ 通配符匹配 (_matches)
- ✅ publish: 持久化 + 分发
- ✅ subscribe / unsubscribe
- ✅ replay: 历史回放
- ✅ recover_subscriptions: 重启恢复
- 🧪 测试: tests/test_event_bus.py (11 passed)

### 5. bus/message_bus.py — [spec 03](specs/03-message-bus.md)
- ✅ 路由检查 (_check_route)
- ✅ from_node 自动填充
- ✅ 多播展开 (list → 多条单发)
- ✅ 内存 asyncio.Queue inbox
- ✅ send / get_inbox / ack / subscribe_inbox
- 🧪 测试: tests/test_message_bus.py (11 passed)

### 6. api/system_api.py — [spec 05](specs/05-api.md)
- ✅ 权限检查框架
- ✅ Node 生命周期 (create_agent / destroy_agent)
- ✅ 人才池管理 (recruit / release)
- ✅ 关系管理
- ✅ 任务 CRUD
- ✅ 消息发送
- 🧪 测试: tests/test_system_api.py (11 passed)

### 7. runtime/agent_runtime.py — [spec 06](specs/06-runtime.md)
- ✅ ADK LlmAgent 配置
- ✅ SystemAPI → ADK tools 映射 (8 个 tools)
- ✅ inbox 桥接 (Queue → ADK)
- ✅ Runner.run_async() 集成

### 8. adapters/cli.py — [spec 07](specs/07-cli-adapter.md)
- ✅ prompt_toolkit 异步输入
- ✅ rich 输出格式化
- ✅ 命令解析 (12 个命令)
- ✅ Tyrant 节点初始化

### 9. __main__.py + 集成验证
- ✅ 启动流程组装 (Store → Bus → API → Runtime → CLI)
- ✅ 模块导入验证通过
- 🧪 全部测试: 75 passed

## 待办

### 11. 非交互式 CLI（命令行协作接口）
- ⬜ 子命令模式（`tyrant-core nodes list`, `tyrant-core message send` 等）
- ⬜ JSON 输出格式（方便外部 agent 系统解析）
- ⬜ 身份认证（--as <node_id> 指定操作身份）
- ⬜ 后续考虑 HTTP API 协作方式

### 12. 子树可达路由
- ✅ 路由规则从“直接关系”扩展为“子树内可达”（与权限模型保持一致）
- ✅ Tyrant 子树 = 整个网络，天然全通
- 🧪 测试通过: 75 passed + 手动验证 Tyrant 可达 manager 的下属

### 10. config.py — 配置系统
- ✅ pydantic-settings Settings 类 (ASA_* 前缀)
- ✅ ~/.config/tyrant_core/.env 配置文件
- ✅ LiteLLM 模型适配 (openai/ 前缀自动添加)
- ✅ apply_env() 环境变量注入
- ✅ __main__.py 集成 Settings
- ✅ runtime 接受 Settings 参数
- ✅ pyproject.toml 添加 litellm 依赖
- 🧪 全部测试: 75 passed

---

## v2 改动计划（递归任务分解）

核心使用场景：Tyrant 向初始默认节点说一句话 → 该节点自动拆解任务、创建/招募人员、分配任务（递归过程）。

### 基础设施层改动

TyrantCore 本身的能力增强，与具体业务场景无关：

#### 13. Task 结果字段
- ✅ Task 模型新增 `result: object` 字段，完成者附上执行结果
- ✅ Store 层 + models.py 同步更新

#### 14. Task 完成自动通知
- ✅ `update_task(status=COMPLETED)` 时触发 EventBus 事件 `task.completed`
- ✅ 系统自动给 Task 创建者发 Message（带 task_id + result）
- ✅ 混合方案：Task 管"做什么/做完没"（结构化骨架），Message 管过程中沟通（灵活通信）

#### 15. Task 查询 tool
- ✅ Agent 可查自己创建的 / 分配给自己的 Task 及状态
- ✅ runtime 新增 ADK tool，减轻 LLM 记忆负担（不靠 context 记"在等谁"）
- 🧪 全部测试: 81 passed

### 默认预设层

基于基础设施之上的默认配置/行为，可替换、可定制：

#### 16. 初始默认节点
- ✅ 启动时自动创建一个"首席助手"节点（Tyrant 的直接下级）
- ✅ Tyrant 开口即有节点接收，无需手动 `/create`
- 这是默认行为，用户可选择不创建或替换为其他节点

#### 17. Agent system prompt（递归分解指导）
- ✅ 默认 prompt 指导：能做就做，不能做就拆分下发，等结果汇总上报
- ✅ 这是 Agent 层的默认行为，不是基础设施强制的
- ✅ Agent 执行模型不变：串行处理 inbox，靠 ADK session 上下文 + Task 查询 tool 维护状态
- 🧪 全部测试: 81 passed + 手动验证通过

---

## v3 改动计划（权限重设计 + 委托机制）

驱动场景：共享服务型节点（如 Agent Creator）需要跨子树操作，当前权限模型（bool + 固定子树作用域）无法支持。

### 基础设施层改动

#### 18. 显式作用域权限模型
- ✅ 权限从 bool 变为带作用域的对象列表
- ✅ 每条权限: action + effect(allow/deny) + scope
- ✅ scope 类型: self_subtree / global / subtrees:[ids] / nodes:[ids]
- ✅ 检查优先级: deny > allow > 默认拒绝
- ✅ Tyrant 隐含 action=* effect=allow scope=global
- ✅ models.py: ScopeType, Scope, PermissionEffect, Permission 新模型
- ✅ store: permissions 存储从 JSON 对象变为 JSON 数组
- ✅ system_api.py: _check_permission(caller, action, target) + _scope_matches 重写
- ✅ grant_permission 改为接受 Permission 对象 + 新增 revoke_permission

#### 19. 委托机制 (Delegation)
- ✅ 新实体 Delegation: delegator, delegate, action, scope, one_time, expires_at
- ✅ 不可再委托（委托获得的权限不能用于创建新 delegation）
- ✅ 权限检查时自动检索匹配的 delegation（转为 allow Permission 参与检查）
- ✅ store: delegations_table + CRUD (save/get/delete/get_for_delegate)
- ✅ system_api: create_delegation / revoke_delegation API
- ✅ runtime: 新增 create_delegation / revoke_delegation tools
- 🧹 全部测试: 94 passed

### 默认预设层

#### 20. Agent Creator 预设
- ⬜ 启动时可选创建 Agent Creator 节点（scope=global 的 create_agent 权限）
- ⬜ 专门的 agent 设计 prompt，能设计复杂 agent 的 instruction/config

---

## v3.5 改动计划（消息查询 + Agent 对话历史可观测性）

驱动场景：节点的收发消息需要可查询，Agent 的 LLM 对话历史（输入/输出/tool call）需要可观测。

框架解耦策略：查询侧完全走自己的 store，runtime 只负责写入。换框架只改 runtime 的 turn 提取逻辑。

### 基础设施层改动

#### 21. ConversationTurn 模型 + 存储
- ✅ models.py: ConversationTurn(id, node_id, role, content, tool_calls, tool_result, trigger_message_id, timestamp)
- ✅ role 细粒度: "user" / "model" / "tool_call" / "tool_result"
- ✅ store: conversation_turns_table + save/query CRUD
- ✅ 索引: idx_turns_node_id, idx_turns_trigger

#### 22. 消息查询 (query_messages)
- ✅ store: query_messages(node_id, from_node, to_node, type, limit) 支持多条件过滤
- ✅ 添加 idx_messages_from 索引
- ✅ system_api: query_messages 带权限检查（查自己允许，查他人需 audit）

#### 23. 对话历史查询 (query_conversation_history)
- ✅ system_api: query_conversation_history 带权限检查（复用 audit 逻辑）
- ✅ 委托 store.query_conversation_turns，不需要 runtime 引用

#### 24. Runtime 写入 turn
- ✅ _agent_loop 中：消息到达时写 user turn，遍历 ADK events 提取 model/tool_call/tool_result turn
- ✅ 唯一的框架耦合点，换框架只改这里

#### 25. Tools + CLI
- ✅ runtime: query_messages / query_conversation_history tools
- ✅ cli: /messages [node_id] + /history <node_id> 命令
- ✅ 测试: store + system_api 层测试
- 🧹 全部测试: 119 passed（含 9 个端到端测试）

---

## v3.5.1 改动计划（回复机制 + turn 去重 + 展示分组）

驱动场景：Agent 的最终回复同时存在于 ConversationTurn 和 Message 中，存在重复；展示层需要以消息为锚点的分组视图。

#### 26. Runtime: final response 不写 model turn
- ✅ _extract_and_save_turns 跳过 is_final_response 的 model text
- ✅ 中间 model 文本（tool call 之间的思考）仍写入
- ✅ 最终回复只存在于自动发送的 reply Message 中

#### 27. Runtime: 回复 Message 加 reply_to reference
- ✅ auto-reply 发出的 Message 的 references 中加 reply_to 指向 trigger message
- ✅ 为展示层分组提供数据关联

#### 28. System prompt 更新
- ✅ 明确说明 auto-reply 机制：你的文本回复会自动发送给消息发送者
- ✅ 明确 send_message 用途：用于主动联系其他节点，不用于回复

#### 29. CLI: /history 分组展示
- ✅ /history 从扁平列表改为按消息分组的树状视图
- ✅ 拉消息 + 按 trigger 拉 turns + 关联 reply_to 的回复

#### 30. 测试更新
- ✅ 更新 e2e 测试: final response 不应出现在 turns 中
- ✅ reply Message 应包含 reply_to reference
- 🧹 全部测试: 120 passed（含 12 个端到端测试）
