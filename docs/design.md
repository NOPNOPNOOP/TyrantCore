# TyrantCore 设计文档

> 一个通信/关系网络基础设施，支持以人为核心、动态扩展的 Agent 组织

---

## 设计哲学

TyrantCore 是**基础设施层**，不是应用层。

我们造公路和交通规则，Agent 自己决定怎么开车。

### 基础设施层（TyrantCore 负责）

- 节点模型 — 让任何可通信对象加入网络
- 关系模型 — 定义节点之间的连接和约束
- 消息协议与路由 — 让节点之间能沟通，控制消息的可达性
- 引用机制 — 统一的实体间引用，支持懒加载
- 任务模型 — 任务的创建、状态追踪
- 系统 API — Agent 创建/销毁、人才池查询/招募、审计
- 权限模型 — 控制谁能调用哪些系统 API
- 事件系统 — pub/sub 事件总线，系统可观测性（append-only 日志）
- 持久化 — 保存状态、记忆、消息
- 容错与恢复 — 重试、恢复、故障上报

### Agent 层（Agent 自行通过 Skill 决定）

- 怎么组织团队、怎么拆分任务
- 怎么招募、面试、评估候选人
- 什么 SOP、什么工作流、什么入职流程
- 记住什么、忘记什么
- 简历的格式和评估标准

Agent 的行为由 Skill（知识/指令单元）驱动。Skill 是 Agent 层面的概念，
类似 Claude Code 的 SKILL.md。基础设施不为 Skill 建模，它只是 Agent 记忆/知识的一部分。

### Agent 不是人类

AI 公司不是人类公司的复刻。设计时需考虑 Agent 的特性：

- **可瞬间创建/销毁/克隆** — 不需要储备人才，不需要面试流程
- **能力由配置决定** — model + tools + skill = 能力，精确可预测
- **无情绪/政治** — 层级纯粹为了信息流和任务分解，不为管理人际关系
- **真正并行** — 默认并行，只在必要时同步
- **上下文窗口有限** — 这是 Agent 真正的带宽瓶颈，层级帮助控制上下文
- **成本按 token 算** — 不是月薪

层级存在的意义：**分解复杂度、控制上下文、质量把关。**

### 内外边界原则

系统内部的状态管理和外部节点的状态管理是分离的：

- **内部节点**：状态、任务、记忆由系统管理
- **外部节点**（IM 人类、外部 Agent）：它们自己的工作流、任务管理是它们自己的事
- 系统只管理外部节点在网络中的连接和消息通道
- 当外部节点的请求流入内部节点时，内部节点自行创建 Task 并通过引用追溯来源

---

## 第一部分：节点（Node）

网络的基本单元。任何可通信对象都是一个节点。

### 节点类型

```
Node（可通信对象）
  ├── HUMAN          — 人类（Tyrant 和其他人类）
  ├── LOCAL_AGENT    — 本地 Agent（系统内创建的）
  └── EXTERNAL_AGENT — 外部 Agent（通过适配器接入）
```

Agent 的工具调用（API、代码执行等）由 Agent 框架层处理，不经过消息总线，不建模为节点。

人类节点的特殊约束：
- 响应速度慢（分钟到天），系统不应阻塞等待
- 需要通知/提醒机制（推送、邮件等）
- 需要 UI（不能只收 JSON）
- endpoint 需要支持人类通信渠道（Slack、邮件、Web UI…）

### 节点数据结构

```yaml
Node:
  id: string              # 唯一标识 (uuid)
  type: enum              # HUMAN / LOCAL_AGENT / EXTERNAL_AGENT
  name: string            # 显示名称
  is_owner: bool          # 是否为网络所有者（Tyrant）
  endpoint:               # 如何联系这个节点
    protocol: string      # "local" / "http" / "websocket" / "adapter"
    address: string       # 地址或适配器ID
    config: object        # 适配器特有配置（可选）
  status: enum            # ONLINE / BUSY / OFFLINE / SUSPENDED
  permissions: object     # 系统级权限（见权限模型）
  metadata: object        # 扩展信息（能力声明、"简历"等，Agent 自由使用）
```

### Tyrant

- Tyrant 是网络的所有者/创建者（人类），系统启动时创建，不可删除
- 拥有全部系统权限
- 网络中**可以有其他人类节点**，权限由 Tyrant 和组织关系决定
- 在节点数据结构中通过 `is_owner: true` 标识

---

## 第二部分：关系（Relation）

节点之间的连接。

### 关系数据结构

统一的关系结构，通过 type 区分语义：

```yaml
Relation:
  id: string
  type: enum              # REPORTS_TO / CONNECTED
  node_a: string          # REPORTS_TO 时为上级，CONNECTED 时顺序无关
  node_b: string          # REPORTS_TO 时为下级
  config: object          # 关系配置（Agent Skill 自定义）
```

### 关系类型

| 类型 | 含义 | 说明 |
|------|------|------|
| REPORTS_TO | 上下级 | node_a 是上级，node_b 是下级，有方向 |
| CONNECTED | 连接 | 双向对等，同一上级的下属自动建立，也可手动建立 |

### 关系约束

| 约束 | 规则 |
|------|------|
| 上级数量 | 单上级（REPORTS_TO 只能有一个） |
| 结构 | 上下级关系形成树，CONNECTED 可跨分支形成图 |
| 循环 | REPORTS_TO 不允许循环 |
| 孤岛 | 不允许，每个节点必须能通过关系链追溯到 Tyrant（create_agent 强制要求 parent） |

### 关系生命周期

**REPORTS_TO** 通过原子操作管理，不单独建立或解除：

| 操作 | 方式 |
|------|------|
| 建立 REPORTS_TO | `create_agent`（创建新 Agent）或 `talent_pool.recruit`（从人才池招募），原子完成节点创建/激活 + 建立关系 |
| 解除 REPORTS_TO | `talent_pool.release`，原子完成解除关系 + 放入人才池 |

**CONNECTED** 通过 `relation.create` / `relation.remove` 管理：

| 操作 | 规则 |
|------|------|
| 建立 CONNECTED | 由上级指定（`relation.create`），或同一上级的下属自动建立 |
| 解除 CONNECTED | 由上级或有权限的节点发起（`relation.remove`） |

不存在"辞职"（Agent 无意愿）和"调岗"（release + recruit 已覆盖）。

### 可见性

每个节点能看到所有与它有直接关系的节点。
更大范围的可见性通过介绍机制或系统权限（audit）获得。

### 上级对直接下级的隐含权限

直接上级对直接下属自动拥有以下权限，无需额外授予：

| 权限 | 说明 |
|------|------|
| 下发任务 | 通过消息发送 |
| 查看状态/记忆 | 隐含 audit 能力，仅限直接下属 |
| 解除关系（开除） | 通过 talent_pool.release |
| 修改关系配置 | 修改 REPORTS_TO 的 config |

`audit` 系统权限用于查看**非直接下属**（子树内其他节点），需要 Tyrant 或上级显式授予。

### 关系配置

配置绑定在**关系**上。基础设施只提供 `config: object` 存储空间，具体字段内容由 Agent Skill 自定义。

---

## 第三部分：通信协议与消息路由

### 路由模型

采用**关系可达模型**：有直接关系的节点之间可以通信，无关系则不可达。

#### 路由规则

| 场景 | 允许条件 |
|------|----------|
| 上级 → 下级 | REPORTS_TO 关系存在 |
| 下级 → 上级 | REPORTS_TO 关系存在 |
| 连接节点互发 | CONNECTED 关系存在 |
| 系统 → 任意节点 | 始终允许 |

#### 不可达时的扩展方式

- **介绍机制**：上级调用 `relation.create(A, B)` 建立 CONNECTED 关系，系统自动通知双方

#### 投递机制

```
发送方 → 系统消息总线 → 路由检查 → 投递到收件箱
```

- **中心化消息总线**：所有消息经过系统，不是点对点
- 系统负责：验证身份（from 由系统填充，不可伪造）、检查路由权限、投递、ACK 管理、持久化
- 每个节点有一个 **inbox**（消息队列），按优先级排序
- Agent 运行时从 inbox 取消息处理

#### 多播

消息的 `to` 字段支持 list。系统拆成多条独立消息分别投递，每条独立 ACK。本质是"批量单发"。

#### 不支持转发

不需要专门的"转发"机制。Agent 可以通过引用原消息 + 创建新消息来实现信息传递。引用机制比转发更高效且不浪费 token。

### 内部 vs 外部消息

| 类型 | 特点 |
|------|------|
| 内部消息 | 系统内节点之间，协议完全可控，支持 ACK 确认 |
| 外部消息 | 通过适配器通信，只能等对方回复，无法保证 ACK |

### 消息基础结构

```yaml
Message:
  # 元信息
  id: string
  timestamp: datetime
  expires_at: datetime        # 过期时间（默认 null，不过期）

  # 路由（from 由系统填充，不可伪造）
  from: string                # 发送者节点ID
  to: string | list           # 接收者（单人或多人）

  # 类型与优先级
  type: string                # 自由定义（基础设施不约束）
  priority: enum              # SYSTEM / URGENT / NORMAL / LOW

  # 内容
  content: object             # 消息内容（格式由 Agent Skill 决定）

  # 引用
  references: list[Reference] # 统一引用（见引用机制）

  # 线程
  thread_id: string           # 对话线程

  # 确认机制（内部消息）
  requires_ack: bool
```

### 消息优先级

```yaml
priority:
  SYSTEM: 0       # 系统级
  URGENT: 1       # 紧急
  NORMAL: 2       # 普通
  LOW: 3          # 低优先级
```

### 消息类型

`type` 为自由字符串，基础设施不约束。以下是推荐的常用类型：

- `TASK` — 下发/请求任务
- `REPORT` — 汇报（进度、结果、问题）
- `QUERY` — 询问/请求信息
- `RESPONSE` — 回复
- `CHAT` — 自由沟通
- `NOTIFICATION` — 通知

Agent 可自定义任意类型。基础设施只关心 `from`（判断是否系统消息）和 `priority`（排序）。

### 引用机制（Reference）

统一的实体间引用，嵌入在 Message 和 Task 的 `references` 字段中。

```yaml
Reference:
  ref_type: enum      # MESSAGE / TASK / NODE
  ref_id: string      # 被引用实体的 ID
  relation: string    # 引用关系: "reply_to" / "origin" / "parent" / "attachment" / "related" / ...
```

- **引用是懒加载的**：只传 ref_id，不传内容。接收方按需获取被引用实体
- **节省 token**：引用代替复制，避免在消息中重复大段内容
- **关系自描述**：`relation` 字段说明引用的语义

### 消息持久化

- 所有消息都存储
- 有 expire 机制（基于 expires_at 字段）

### 事件系统（Event）

事件与消息是两个独立的机制：
- **消息**：有发送方和接收方，受路由规则约束，用于节点间通信
- **事件**：有来源但无指定接收方，pub/sub 模式，用于系统可观测性

```yaml
Event:
  id: string
  type: string          # "node.status_changed" / "task.completed" / 自定义
  timestamp: datetime
  source: string        # 触发来源（节点ID 或 "system"）
  data: object          # 事件数据
```

#### 系统事件（系统自动发布）

| 事件 | 触发时机 |
|------|----------|
| node.status_changed | 节点状态变化（ONLINE → OFFLINE 等） |
| relation.created | 新关系建立 |
| relation.removed | 关系解除 |
| task.status_changed | 任务状态变化 |
| node.destroyed | 节点被销毁 |
| permission.granted | 权限被授予 |
| agent.crashed | Agent 执行异常 |

Agent 也可通过 API 发布自定义事件（如 "build.completed"、"anomaly.detected"）。

#### 订阅

- 节点订阅自己关心的事件类型
- 不受关系路由约束（事件是广播，不是通信）
- 权限控制：订阅范围受子树约束（不能订阅子树外节点的事件，除非有 audit 权限）

#### 持久化

- Append-only 事件日志，支持从指定时间点回放
- 离线节点醒来后可消费错过的事件
- 保留策略由系统配置（如保留最近 N 天）

---

## 第四部分：任务（Task）

Task 是独立的一等实体。**Task 由 Agent 创建（通过系统 API），不由消息自动产生。**

Message 是请求（"我要你做 X"），Task 是内部工作追踪（"我正在做 X"）。
收到请求后，Agent 自行决定是否创建 Task、如何分解。

```yaml
Task:
  id: string
  creator: string             # 谁创建的（创建 Task 的内部节点）
  assignee: string            # 谁负责
  participants: list[string]  # 参与者（含 assignee），纯记录字段
  status: enum                # PENDING / IN_PROGRESS / BLOCKED / COMPLETED / FAILED
  content: object             # 任务内容（自然语言 + 结构化，格式由 Agent 决定）
  parent_task: string         # 父任务（支持任务分解）
  references: list[Reference] # 引用（来源消息、相关文件、请求者等）
  created_at: datetime
  updated_at: datetime
```

任务的 `references` 用于追溯来源（origin）、关联文件（attachment）等。没有引用的 Task 视为 Agent 自驱创建。

### Task API

| API | 作用 |
|------|------|
| task.create | 创建任务 |
| task.update | 更新任务状态/内容 |
| task.query | 查询任务（按 assignee、status 等过滤） |

任务的分解策略、验收标准、执行方式由 Agent 自行决定。

---

## 第五部分：系统 API 与权限

### 系统 API

原来的"特殊角色"（Agent God、HR、监察）不再是固定节点，而是系统提供的能力。
任何拥有对应权限 + 对应 Skill 的节点都可以使用这些 API。

| API | 作用 |
|------|------|
| create_agent | 创建新 Agent（原子：创建节点 + 建立 REPORTS_TO，强制要求 parent） |
| destroy_agent | 销毁 Agent |
| talent_pool.query | 查询人才池 |
| talent_pool.recruit | 从人才池招募（原子：激活 Agent + 建立 REPORTS_TO） |
| talent_pool.release | 释放 Agent（原子：解除 REPORTS_TO + 放入人才池） |
| audit.inspect | 查看节点的状态/记忆（直接上级隐含，或需 audit 权限） |
| relation.create | 建立 CONNECTED 关系（由上级指定） |
| relation.remove | 解除 CONNECTED 关系 |
| task.create / update / query | 任务管理 |

### 权限模型

> **注意：以下为 v1 权限模型，v3 已重新设计，见末尾「v3 演进」章节。**

权限挂在节点上，由上级授予，可下放。Tyrant 天生拥有全部权限。

权限作用域固定为节点自身的子树。Tyrant 的子树即整个网络，因此等效于全局。

```yaml
Permissions:
  create_agent: bool
  destroy_agent: bool
  recruit: bool
  audit: bool              # 查看子树内非直接下属的状态/记忆
  grant_permission: bool
```

### 权限规则

- **不可自提权**：节点不能给自己授予新权限，需要 Tyrant 或上级授予
- **下放限制**：只能下放自己拥有的权限
- **销毁后的权限链**：中间节点被销毁后，它曾授予的权限默认保留，系统发出通知。有 audit 权限的节点可事后审查和撤销

---

## 第六部分：人才池

基础设施提供人才池的存储和查询能力。

### 人才池结构

```yaml
TalentPool:
  internal:                   # 内部人才池（初始为空）
    - 被释放的 Agent 实例（记忆/状态持久化在文件中）

  external:                   # 外部人才市场（通过网络接入，可配多个）
    - market_id: string
      name: string
      adapter: string
      catalog: list           # 静态配置 + 动态查询 都支持
```

基础设施提供：
- 人才池的存储和查询接口
- 节点 metadata 的搜索接口（Agent 可在 metadata 中存储"简历"等信息）
- 外部市场的适配器接口

简历的格式、筛选方式、面试流程由 Agent 的 Skill 自行决定。

---

## 第七部分：外部接入与适配器

### 适配器（Adapter）

适配器是**传输层**，不是节点。外部实体（人类、外部 Agent）有自己的节点，适配器是它们的 endpoint 实现。

统一模式：
- HUMAN 节点：endpoint 指向 IM 适配器
- EXTERNAL_AGENT 节点：endpoint 指向 Agent 适配器
- 适配器负责消息格式转换和协议翻译，不做决策

Agent 适配器额外负责：
- 管理上下文/记忆（为无状态的外部 Agent 维护本地状态）
- 暴露工具给外部 Agent（如 delegate_to_subordinate）

外部 Agent 的配置存在节点的 endpoint 和 metadata 中：

```yaml
# 外部 Agent 节点示例
Node:
  id: string
  type: EXTERNAL_AGENT
  name: "Claude Assistant"
  endpoint:
    protocol: adapter
    address: agent_adapter_01
    config:                     # 适配器特有配置
      service_type: "claude"    # "openai" / "claude" / "ollama" / ...
      model: "claude-sonnet"
      connection: object        # API key、endpoint 等
  metadata:
    local_memory: object        # 本地维护的记忆
    context_window: list        # 发送给外部 Agent 的上下文
```

### IM 适配器

人类节点通过 IM（Slack、Discord、Telegram、Web UI 等）接入系统。

适配器是**纯透明桥梁**，不做决策。"理解人类说了什么"的职责交给接收消息的 Agent（它本身就是 LLM）。

IM 适配器的职责：

- **消息传递**：IM → 系统：把 IM 文本包装成 Message（type: "CHAT"，content 放原始文本）；系统 → IM：把结构化消息渲染成人类可读格式
- **会话映射**：IM 的 thread → 系统的 thread_id
- **身份映射**：IM user ID ↔ 系统 Node ID
- **一个适配器服务多个人类**：一个 Slack workspace adapter 管理多个人类节点
- **Hook 机制**：提供 `on_receive` / `on_send` / `on_format` 扩展点，具体逻辑由使用者注入

通过 IM 适配器，Tyrant 和其他人类可以：
- 与任意（有关系的）Agent 直接对话
- 将外部人类拉入网络（创建 HUMAN 节点 + 建立关系）
- 接收通知和汇报

### 外部节点原则

- 外部节点（IM 人类、外部 Agent）的工作流和任务管理是它们自己的事
- 系统不为外部节点创建 Task 实体
- 外部节点发来的消息是普通 Message，内部节点收到后自行决定是否创建 Task
- 内部 Task 通过 references 追溯到外部来源

---

## 第八部分：Agent 运行模型

### 初始方案：常驻进程

每个 LOCAL_AGENT 节点对应一个常驻运行的进程/协程：
- 持续监听 inbox
- 收到消息后处理
- 空闲时等待

### 后续演进：按需唤醒

当 Agent 数量增长后，迁移到按需唤醒模型：
- Agent 平时"休眠"（无进程，状态持久化在磁盘）
- 收到消息时由调度器唤醒
- 处理完后重新休眠
- 需要一个 **调度器（Scheduler）** 组件

按需唤醒更符合"成本按 token 算"的哲学，但增加了实现复杂度，适合第二阶段。

---

## 第九部分：容错与恢复

### Agent 崩溃

1. **检测**：消息总线检测到 Agent 无响应（ACK 超时）
2. **重试**：指数退避重试（1s → 2s → 4s → ...）
3. **恢复**：从持久化状态恢复 Agent，重新投递未处理的消息
4. **上报**：发送系统通知（agent.crashed），通知上级节点
5. **放弃**：达到最大重试次数后标记为 FAILED，上级决定后续处理

### 系统重启

- 从持久化存储恢复所有节点状态、关系、未完成的 Task
- 重新启动所有 ONLINE 状态的 Agent 进程
- 重新投递所有未 ACK 的消息

### ACK 超时

- 内部消息：指数退避重发，达到上限后发送系统通知通知发送方
- 外部消息：无 ACK 机制，只能等回复

---

## 第十部分：安全模型

### 消息身份验证

- `from` 字段由系统自动填充，节点不可自行设置或伪造
- 消息总线在投递前验证 from 和路由权限

### 权限控制

- 不可自提权（见权限模型）
- 权限通过系统 API 授予，有完整的操作日志

### 销毁后的权限链

- 中间节点被销毁后，系统发送 node.destroyed 通知
- 该节点曾授予的权限默认保留（不级联撤销）
- 有 audit 权限的节点可审查并手动撤销

---

## 设计决策汇总

### 基础设施层

| 方面 | 决定 |
|------|------|
| 核心实体 | Node、Relation、Message、Task（4 个） |
| 消息路由 | 关系可达模型 |
| 消息投递 | 中心化消息总线，inbox 队列，按优先级排序 |
| 消息身份 | from 由系统填充，不可伪造 |
| 消息类型 | type 为自由字符串，基础设施不约束 |
| 消息持久化 | 全部存储，有 expire 机制 |
| 引用机制 | 统一 Reference，懒加载，嵌入在 Message/Task 中 |
| 任务模型 | 独立一等实体，Agent 通过 API 创建，Message 不自动产生 Task |
| 关系类型 | REPORTS_TO（上下级树）+ CONNECTED（对等图），统一数据结构 |
| 关系操作 | REPORTS_TO 通过原子 API（create_agent / recruit / release）管理；CONNECTED 通过 relation.create / remove |
| 关系配置 | 绑定在关系上，config: object 由 Agent Skill 自定义 |
| 上级权限 | 直接上级隐含 audit，非直接下属需显式授予 audit 权限 |
| 适配器 | 传输层，不是节点，不做决策 |
| Agent 创建 | create_agent 原子操作（创建节点 + 建立 REPORTS_TO） |
| 权限 | 布尔开关，作用域固定为自身子树 |
| 人才池 | 内部（初始空）+ 外部（可配多市场） |
| 事件系统 | 独立 pub/sub 机制，append-only 日志，与消息系统分离 |
| Agent 运行 | 初期常驻进程，后续按需唤醒 |
| 容错 | 指数退避重试 → 恢复 → 通知上级 → 放弃 |
| 外部节点 | 内部管内部，外部管外部 |
| 通信 | 异步，内部有 ACK，外部等回复 |
| 持久化 | 文件存储 |
| 记忆 | 每个 Agent 独立记忆 |

### Agent 层（由 Skill 驱动）

| 方面 | 说明 |
|------|------|
| 团队组织 | Agent 自己决定 |
| 招募/面试 | 有 recruit 权限的 Agent 自行决定 |
| 简历格式 | Agent 用 metadata 自定义 |
| 入职流程 / SOP | Agent 的 Skill |
| 任务分解 / 进度追踪 / 失败处理 | Agent 自己决定 |
| 审计方式 | 有 audit 权限的 Agent 自行决定 |

---

## 架构图

```
                    ┌─────────────┐
                    │   Tyrant    │  (网络所有者，全部权限)
                    │ (IM Adapter)│
                    └──────┬──────┘
                           │ REPORTS_TO
                    ┌──────┴──────┐
                    │  Top Agent  │  (普通节点)
                    └──────┬──────┘
                           │ REPORTS_TO
            ┌──────────────┼──────────────┐
            │              │              │
     ┌──────┴──────┐ ┌─────┴─────┐ ┌──────┴──────┐
     │ Agent A     │ │ Agent B   │ │  Adapter    │
     │             │ │(有recruit │ │ (外部Agent) │
     │             │ │ 权限)     │ │             │
     └──────┬──────┘ └───────────┘ └─────────────┘
            │ CONNECTED (跨分支)
            └────────────────────────────┘

     ┌────────────────────────────────────────┐
     │            System Layer                  │
     │  消息总线 / 系统 API                     │
     │  create_agent / task / audit / ...      │
     └────────────────────────────────────────┘
```

---

## 提议（暂不实现）

- 先知能力（任务规划顾问 / 风险预警 / 模式学习）
- Agent 克隆（复制配置和 Skill）
- 经验合并（把 A 的经验给 B）
- ROI 评估（成本管理）
- Agent 晋升机制
- 按需唤醒调度器
- 文档存储（独立 Document 实体）
- Task 临时通道（participants 作为路由依据）

---

## 典型场景

**场景：Tyrant 通过 Slack 让 Top Agent "写一个爬虫项目"**

1. Tyrant 在 Slack 发消息 → IM Adapter 转为 Message（from: tyrant, to: top_agent, type: "CHAT"，content: 原始文本）
2. Top Agent 收到消息，调用 task.create 创建 Task（references 指向 Tyrant 的消息）
3. Top Agent 分析任务，发现需要 "代码编写"、"测试"
4. Top Agent 有 recruit 权限，调用 talent_pool.query 查找合适的 Agent
5. 没有合适的 → 调用 create_agent 创建新 Agent（原子完成：创建节点 + 建立 REPORTS_TO + 注入 Skill）
6. 发送 type: "TASK" 消息给新 Agent
7. 新 Agent 收到消息，调用 task.create 创建子 Task（parent_task 指向上级 Task）
8. 新 Agent 执行任务，通过 type: "REPORT" 消息汇报进度
9. 完成后 Top Agent 更新 Task 状态，向 Tyrant 发送 type: "REPORT" 消息
10. Tyrant 在 Slack 收到汇报（IM Adapter 格式化为可读文本）
11. 如需释放 Agent → 调用 talent_pool.release（原子完成：解除 REPORTS_TO + 放入人才池）

---

## 待讨论

1. 技术选型最终确定
2. 并发控制具体方案（乐观锁？原子操作？）
3. 存储方案细化（SQLite vs 纯文件 vs 混合）

---

## v2 演进：递归任务分解

### 使用场景

Tyrant 向初始默认节点说一句话 → 该节点自动拆解任务、创建/招募人员、分配任务。如果子节点不需要继续分解则自己执行，否则继续递归。

### 基础设施层改动

以下是 TyrantCore 基础设施本身的能力增强，与具体业务场景无关：

**1. Task 结果字段**
Task 模型新增 `result: object`，完成者附上执行结果。

**2. Task 完成自动通知（混合方案）**
- Task 管"做什么/做完没"——结构化骨架，系统可追踪
- Message 管过程中的沟通——灵活，不受约束
- `update_task(status=COMPLETED)` → EventBus 事件 `task.completed` → 系统自动给 Task 创建者发 Message（带 task_id + result）

**3. Task 查询 tool**
Agent 可通过 tool 查询自己创建的/分配给自己的 Task 及状态，减轻 LLM 的记忆负担。Agent 不靠 context 记"我在等谁"，随时可查。

### 默认预设层

以下是基于基础设施之上的默认配置/行为，可替换、可定制：

**4. 初始默认节点**
启动时自动创建一个"首席助手"节点作为 Tyrant 的直接下级。这是默认行为，不是基础设施约束——用户可以选择不创建或替换为其他节点。

**5. Agent system prompt（递归分解指导）**
默认 prompt 指导递归分解逻辑：能做就做，不能做就拆分（create_agent/recruit + 创建子 Task + 发消息下发），等结果汇总上报。这是 Agent 层的默认行为，不是基础设施强制的。

### Agent 执行模型（不变）

保持现有串行 inbox 处理。Agent 的 inbox 是 queue，消息按顺序处理，ADK session 保持对话上下文。LLM 通过 session 历史 + Task 查询 tool 理解当前状态，决定如何反应（回复中间问题、记录完成通知、汇总上报）。

---

## v3 演进：权限重设计 + 委托机制

驱动场景：共享服务型节点（如 Agent Creator）需要跨子树操作。v1 的 bool 权限 + 固定子树作用域无法支持。

### 显式作用域权限模型

权限从 bool 变为带作用域和效果的对象列表：

```yaml
Permission:
  action: string           # "create_agent" / "audit" / "*" / ...
  effect: "allow" | "deny"
  scope:
    type: "self_subtree" | "global" | "subtrees" | "nodes"
    targets: list[string]  # 仅 subtrees/nodes 时使用
```

节点的 permissions 为 Permission 列表，通过多条条目实现组合。

**scope 类型：**
- `self_subtree` — 自身子树（v1 行为）
- `global` — 全局
- `subtrees: [A, B]` — A 和 B 的整棵子树内
- `nodes: [A, B]` — 精确到 A 和 B 这两个节点

**检查流程（deny 优先）：**
1. 收集节点自身 permissions + 收到的 delegations 中所有匹配 action 的条目
2. 任何 deny 命中目标 → 拒绝
3. 任何 allow 命中目标 → 放行
4. 都没命中 → 拒绝

Tyrant 隐含 `Permission(action="*", effect="allow", scope=global)`，不需显式存储。

### 委托机制 (Delegation)

临时/持久的授权转移，支持跨子树操作：

```yaml
Delegation:
  id: string
  delegator: string        # 谁授权
  delegate: string         # 谁被授权
  action: string           # 授权的动作
  scope:                   # 不能超出 delegator 自身权限范围
    type: string
    targets: list[string]
  one_time: bool           # 一次性还是持久
  expires_at: datetime     # 过期时间（可空）
```

**约束：**
- 不可再委托：A 委托给 B，B 不能把这个委托转给 C
- 委托范围 ≤ 委托者自身权限范围

**典型场景：**
Agent A 想让 Agent Creator 帮自己建 agent → A 创建 delegation(delegate=Creator, action="create_agent", scope=nodes:[A]) → Creator 拿着委托调 create_agent(parent_id=A)

---

## v3.5 演进：消息查询 + Agent 对话历史可观测性

驱动场景：节点的收发消息需要可查询，Agent 内部的 LLM 对话历史（输入/输出/tool call）需要可观测。

### 框架解耦原则

对话历史不从 Agent 框架（如 ADK）的 session 中读取，而是由 runtime 写入我们自己的 store：

```
查询侧：  SystemAPI → Store                 ← 不碰框架
写入侧：  Runtime → ADK events → Store      ← 唯一耦合点
```

换框架只改 runtime 的 event 提取逻辑，查询侧零改动。

### 回复机制：Auto-reply

Agent 收到消息后经过 ReAct 过程，LLM 的最终文本输出**自动作为回复 Message 发送给原消息发送者**。

这是默认的回复通道，类似聊天软件——你给我发消息，我的回复自然回给你，不需要额外操作。

- `send_message` tool 用于**主动联系其他节点**（下发任务、跨节点协调），不用于回复当前消息发送者
- 回复 Message 通过 `references` 中的 `reply_to` 关联原消息
- 系统不做「已发送检测」——Agent 通过 system prompt 理解这个机制，自然回答即可

### ConversationTurn

Agent 内部 LLM 交互的**过程记录**，只记录 ReAct 思考链，不记录最终回复：

```yaml
ConversationTurn:
  id: string
  node_id: string               # 所属 agent
  role: string                  # "user" | "tool_call" | "tool_result" | "model"(仅中间思考)
  content: string               # 文本内容
  tool_calls: list[object]      # role=tool_call 时：[{name, args}]
  tool_result: object           # role=tool_result 时：{name, result}
  trigger_message_id: string    # 触发的 TyrantCore Message ID（可空）
  timestamp: datetime
```

**与 Message 的关系（无重复）：**
- **ConversationTurn** 记录 Agent 的思考过程（输入 + ReAct 中间步骤）
- **Message** 记录实际通信（收到的消息 + 发出的回复）
- LLM 的最终回复（`is_final_response`）**不写 turn**，它直接成为回复 Message
- 中间 model 文本（tool call 之间的思考）仍记录为 turn
- turn 序列：`user → [tool_call → tool_result]* → (可选的中间 model 思考)`
- 通过 `trigger_message_id` 关联：一条 Message 触发一组 turn + 一条回复 Message

### 展示模型：以消息为锚点的分组视图

展示层按 `trigger_message_id` 分组，以收到的消息为锚点，挂载 ReAct 过程和回复：

```
📨 tyrant → 首席助手: "帮我查任务" (12:00:00)
  ├─ 🔧 query_tasks({assignee: ...})
  ├─ 📎 {tasks: [...]}
  └─ 💬 首席助手 → tyrant: "这是你的任务列表：..." (reply)

📨 tyrant → 首席助手: "你好" (12:00:10)
  └─ 💬 首席助手 → tyrant: "你好，有什么需要？" (reply)
```

**查询 API 保持原子化（展示层自行组装）：**
- `query_messages` 查消息列表
- `query_conversation_turns(trigger_message_id=xxx)` 查关联的 turns
- 展示层拉消息 → 按 trigger 拉 turns → 找到 reply_to 的回复 → 组装树状视图
- 符合基础设施哲学：基础设施提供数据和关联，展示层自行决定呈现方式

### 消息查询 API

支持查询节点的收发消息记录：

```
query_messages(node_id, from_node, to_node, type, limit)
```

- `node_id`：节点参与的所有消息（from OR to 匹配）
- 可组合过滤：`from_node`、`to_node`、`type`

### 权限控制

消息查询和对话历史查询复用 audit 权限模型：

| 场景 | 权限 |
|------|------|
| 查自己的消息/对话历史 | 始终允许 |
| 查直接下属的 | 隐含允许（上级权限） |
| 查其他节点的 | 需要 audit 权限（对目标节点） |
