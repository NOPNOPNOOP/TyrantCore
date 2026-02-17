# TyrantCore 架构文档

> 通信/关系网络基础设施，支持以人为核心、动态扩展的 AI Agent 组织

## 设计哲学

TyrantCore 是**基础设施层**——造公路和交通规则，Agent 自己决定怎么开车。

**基础设施层（TyrantCore 负责）：** 节点模型、关系模型、消息协议与路由、引用机制、任务模型、系统 API、权限模型、事件系统、持久化、容错与恢复。

**Agent 层（Agent 自行决定）：** 团队组织、招募/面试、SOP/工作流、记忆管理、简历格式。

### Agent 不是人类

- **可瞬间创建/销毁/克隆** — 不需要储备人才
- **能力由配置决定** — model + tools + skill = 能力
- **无情绪/政治** — 层级纯粹为了信息流和任务分解
- **真正并行** — 默认并行，只在必要时同步
- **上下文窗口有限** — 层级帮助控制上下文
- **成本按 token 算** — 不是月薪

层级存在的意义：**分解复杂度、控制上下文、质量把关。**

### 内外边界原则

- **内部节点**：状态、任务、记忆由系统管理
- **外部节点**（IM 人类、外部 Agent）：它们自己的工作流是它们自己的事
- 系统只管理外部节点在网络中的连接和消息通道

---

## 核心实体

### Node（节点）

网络的基本单元，任何可通信对象都是一个节点。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | uuid4 |
| type | enum | HUMAN / LOCAL_AGENT / EXTERNAL_AGENT / TERMINAL |
| name | string | 显示名称（ADK 要求合法标识符） |
| is_owner | bool | 是否为 Tyrant |
| endpoint | object | protocol + address + config |
| status | enum | ONLINE / BUSY / OFFLINE / SUSPENDED |
| permissions | list[Permission] | 显式作用域权限列表 |
| tags | list[str] | 服务发现标签 |
| metadata | object | 扩展信息（agent_config 等） |

**Tyrant**：网络所有者（is_owner=True），隐含全部权限，不可删除。

### Relation（关系）

| 类型 | 含义 | 方向 |
|------|------|------|
| REPORTS_TO | 上下级 | node_a=上级, node_b=下级 |
| CONNECTED | 对等连接 | 双向 |

**约束：** 单上级、树形结构、不允许循环、不允许孤岛。

**生命周期：** REPORTS_TO 通过原子 API（create_agent / recruit / release）管理，不能单独建立。CONNECTED 通过 relation.create / remove 管理，同一上级的下属自动建立。

### Message（消息）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | uuid4 |
| from_node | string | 系统自动填充，不可伪造 |
| to | string | 接收者 |
| type | string | 自由定义（CHAT/TASK/REPORT/NOTIFICATION 等） |
| priority | enum | SYSTEM / URGENT / NORMAL / LOW |
| content | object | 消息内容 |
| references | list[Reference] | 引用（reply_to / origin 等） |
| thread_id | string | 对话线程 |
| requires_ack | bool | 是否需要确认 |

### Task（任务）

独立一等实体，由 Agent 通过 API 创建，Message 不自动产生 Task。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | uuid4 |
| creator | string | 创建者 |
| assignee | string | 负责人 |
| status | enum | PENDING / IN_PROGRESS / BLOCKED / COMPLETED / FAILED |
| content | object | 任务内容 |
| result | object | 执行结果 |
| parent_task | string | 父任务（支持递归分解） |
| references | list[Reference] | 引用 |

### Event（事件）

与消息独立的 pub/sub 机制，用于系统可观测性。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | uuid4 |
| type | string | 事件类型（node.status_changed 等） |
| source | string | 触发来源 |
| data | object | 事件数据 |

### ConversationTurn（对话历史）

Agent 内部 LLM 交互的过程记录，与 Message 无重复（final response 不写 turn，直接成为 reply Message）。

| 字段 | 类型 | 说明 |
|------|------|------|
| node_id | string | 所属 agent |
| role | string | user / model / tool_call / tool_result |
| trigger_message_id | string | 触发的 Message ID |

---

## 通信协议

### 路由模型

**子树可达模型**：同一子树内的节点可以通信。Tyrant 子树 = 整个网络，天然全通。

| 场景 | 允许条件 |
|------|----------|
| 子树内节点互发 | 在同一子树内 |
| 系统 → 任意节点 | 始终允许 |

### 投递机制

```
发送方 → 消息总线 → 路由检查 → inbox（按优先级排序）
```

- 中心化消息总线，from 由系统填充不可伪造
- 多播：to 为 list 时拆成多条独立消息分别投递

### 回复机制（Auto-reply）

Agent 的 LLM 最终文本输出**自动作为回复 Message 发送给原消息发送者**，带 reply_to reference。send_message tool 用于主动联系其他节点。

---

## 权限模型

### 显式作用域权限

```yaml
Permission:
  action: string           # "create_agent" / "audit" / "*"
  effect: "allow" | "deny"
  scope:
    type: "self_subtree" | "global" | "subtrees" | "nodes"
    targets: list[string]  # 仅 subtrees/nodes 时使用
```

**检查流程（deny 优先）：** 收集自身 permissions + delegations → deny 命中则拒绝 → allow 命中则放行 → 默认拒绝。

**隐含权限：** Tyrant 隐含全局 allow；直接上级对直接下属隐含 audit。

### 委托机制（Delegation）

临时/持久的授权转移，支持跨子树操作。不可再委托。

---

## 服务发现

Node.tags 标记系统服务节点，Agent 通过 list_nodes(tags=...) 查找可用服务。

### Session 模式

| 模式 | 说明 |
|------|------|
| persistent（默认） | 共享 ADK session，保持对话上下文 |
| per_request | 每条消息新建临时 session，处理完丢弃 |

通过 agent_config.session_mode 配置。

---

## 模块依赖图

```
cli_adapter ──→ system_api
                   │
         ┌─────────┼─────────┐
         ▼         ▼         ▼
   message_bus  event_bus  agent_runtime
         │         │         │
         ▼         ▼         ▼
        store     store    message_bus
         │                 event_bus
         ▼                 adk
       models
```

实现顺序：models → store → bus → api → runtime → cli

---

## 架构图

```
                    ┌─────────────┐
                    │   Tyrant    │  (网络所有者，全部权限)
                    │ (CLI/IM)    │
                    └──────┬──────┘
                           │ REPORTS_TO
              ┌────────────┼────────────┐
              │            │            │
       ┌──────┴──────┐ ┌──┴───┐ ┌──────┴──────┐
       │  首席助手   │ │agent_│ │  其他节点   │
       │ (persistent)│ │creator│ │             │
       └──────┬──────┘ │(per_ │ └─────────────┘
              │        │request│
              │        └──────┘
       ┌──────┴──────┐
       │  子节点...  │
       └─────────────┘

     ┌────────────────────────────────────────┐
     │            System Layer                 │
     │  消息总线 / 系统 API / 事件总线         │
     └────────────────────────────────────────┘
```
