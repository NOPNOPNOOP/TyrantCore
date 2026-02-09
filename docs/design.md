# TyrantCore 设计文档

> 一个通信/关系网络基础设施，支持以人为核心、动态扩展的Agent组织

---

## 设计哲学

TyrantCore 是**基础设施层**，不是应用层。

我们造公路和交通规则，Agent自己决定怎么开车。

### 基础设施层（TyrantCore 负责）

- 节点模型 — 让任何可通信对象加入网络
- 关系模型 — 定义节点之间的连接和约束
- 消息协议 — 让节点之间能沟通
- 人才池 — 提供发现和招募的能力
- Agent创建/销毁 — Agent God 工厂机制
- 持久化 — 保存状态、记忆、消息

### Agent层（Agent 自行决定）

- 怎么组织团队、怎么拆分任务
- HR 怎么面试、怎么评估候选人
- 创造什么样的 Agent
- 什么 SOP、什么工作流
- 什么入职流程
- 记住什么、忘记什么

Agent 的行为由 Skill（知识/指令单元）驱动。Skill 是 Agent 层面的概念，
类似 Claude Code 的 SKILL.md：一个包含 YAML frontmatter 和 markdown 指令的文件，
Agent 根据上下文自主决定何时使用哪个 Skill。
基础设施不需要为 Skill 建模，它只是 Agent 记忆/知识的一部分。

---

## 第一部分：节点（Node）

网络的基本单元。任何可通信对象都是一个节点。

### 节点类型

```
Node（可通信对象）
  ├── HUMAN          — 人类
  ├── LOCAL_AGENT    — 本地 Agent（系统内创建的）
  ├── EXTERNAL_AGENT — 外部 Agent（通过适配器接入）
  └── SERVICE        — 服务（工具、API 等）
```

### 节点数据结构

```yaml
Node:
  id: string              # 唯一标识 (uuid)
  type: enum              # HUMAN / LOCAL_AGENT / EXTERNAL_AGENT / SERVICE
  name: string            # 显示名称
  capabilities: list      # 能力声明 ["coding", "research", "translation"]
  endpoint:               # 如何联系这个节点
    protocol: string      # "local" / "http" / "websocket" / "adapter"
    address: string       # 地址或适配器ID
  status: enum            # ONLINE / BUSY / OFFLINE / SUSPENDED
  metadata: object        # 扩展信息
```

### Tyrant

- Tyrant 是网络的所有者/创建者（人类）
- 拥有全局权限：查看任何节点状态/记忆、干预任何层级、直接操作 Agent God
- Tyrant 的网络中**可以有其他人类节点**，其他人类的权限由 Tyrant 和组织关系决定
- 除 Tyrant 外，所有角色（Top Agent、HR、监察等）都只是普通节点，没有系统级特殊待遇

---

## 第二部分：关系（Relation）

节点之间的连接。

### 关系数据结构

```yaml
Relation:
  id: string
  from_node: string       # 节点ID
  to_node: string         # 节点ID
  type: enum              # 关系类型
  config: object          # 关系配置（绑定在关系上，不是节点上）
```

### 关系类型

| 类型 | 含义 | 通信方向 |
|------|------|----------|
| SUPERIOR | 上级 | 双向 |
| SUBORDINATE | 下级 | 双向 |
| PEER | 同级协作伙伴 | 双向 |
| ACQUAINTANCE | 认识但非直接协作 | 单向发起 |

### 关系约束

| 约束 | 规则 |
|------|------|
| 上级数量 | 单上级 |
| 兼职 | 不允许（全职独占） |
| 结构 | 严格树状 |
| 循环 | 不允许 |

### 关系生命周期

| 操作 | 规则 |
|------|------|
| 上下级建立 | 上级/HR 单方面决定 |
| 同级关系 | 自动建立（同上级自动互为同级） |
| 开除 | 上级单方面决定 |
| 辞职 | 不允许 |
| 调岗/换上级 | 不允许 |

### 关系可见性

- **最小可见性**：每个节点只能看到直接上级、直接下级、已知同级
- 跨分支节点必须通过共同上级介绍才能认识

### 上级对下级的权限

| 权限 | 是否拥有 |
|------|----------|
| 下发任务 | ✅ |
| 查看状态 | ✅ |
| 查看记忆 | ✅ |
| 开除 | ✅ |
| 修改配置 | ✅ |

### 关系配置

配置绑定在**关系**上，而不是节点上。同一个下级对不同上级可以有不同的沟通方式。

```yaml
RelationConfig:
  negotiable: bool        # 是否可协商（vs 纯命令）
  decision_mode: enum     # LEADER_DECIDE / VOTE
  allow_skip_level: bool  # 是否允许越级汇报
```

---

## 第三部分：通信协议

### 内部 vs 外部消息

| 类型 | 特点 |
|------|------|
| 内部消息 | 系统内节点之间，协议完全可控，支持 ACK 确认 |
| 外部消息 | 通过社交网络/适配器通信，只能等对方回复，无法保证 ACK |

### 消息基础结构

```yaml
Message:
  # 元信息
  id: string
  timestamp: datetime
  expires_at: datetime        # 过期时间（有 expire 机制，暂时无限）

  # 路由
  from: string                # 发送者节点ID
  to: string | list           # 接收者（单人或多人）
  reply_to: string            # 回复哪条消息（可选）

  # 类型与优先级
  type: enum
  priority: enum

  # 内容（混合格式：结构化字段 + 自然语言字段）
  content: object

  # 上下文
  context:
    task_id: string
    meeting_id: string
    thread_id: string

  # 确认机制（内部消息）
  requires_ack: bool
  ack_received: bool
  ack_timeout: int            # 超时时间（秒）
```

### 消息优先级

```yaml
priority:
  SYSTEM: 0       # 系统级（状态同步等实时信息）
  ALERT: 1        # 紧急警报
  ESCALATE: 2     # 问题上报
  TASK: 3         # 任务相关
  NORMAL: 4       # 普通消息
  LOW: 5          # 低优先级
```

### 消息类型总览

#### 任务流

| 类型 | 方向 | 用途 |
|------|------|------|
| TASK | 上→下 | 下发任务 |
| TASK_UPDATE | 上→下 | 修改任务（改优先级、改要求、取消） |
| REPORT | 下→上 | 汇报进度/结果/问题 |
| ESCALATE | 下→上 | 问题上报（无法解决） |

#### 沟通

| 类型 | 方向 | 用途 |
|------|------|------|
| QUERY | 任意 | 询问/请求信息 |
| RESPONSE | 任意 | 回复询问 |
| CHAT | 同级 | 自由讨论 |

#### 会议

| 类型 | 方向 | 用途 |
|------|------|------|
| MEETING_START | 上→下(多人) | 召集会议 |
| MEETING_MESSAGE | 会议内 | 会议中发言 |
| MEETING_END | 上→下(多人) | 结束会议 |

#### 组织

| 类型 | 方向 | 用途 |
|------|------|------|
| INTRODUCE | 上→下(多人) | 介绍新同事/协作者 |
| ASSIGNMENT | HR/上级→下 | 通知被分配给某上级 |
| DISMISSAL | 上→下 | 通知被开除 |

#### 资源请求

| 类型 | 方向 | 用途 |
|------|------|------|
| REQUEST_HIRE | 任意→HR | 招人请求 |
| REQUEST_TOOL | 下→上 | 请求工具/资源 |

#### 状态同步

| 类型 | 用途 |
|------|------|
| STATUS_UPDATE | 节点状态变化（忙→空闲，上线→下线） |
| CAPABILITY_UPDATE | 能力变化（学会新技能、获得新工具） |

#### 协作

| 类型 | 用途 |
|------|------|
| DELEGATE | 请同级帮忙处理部分任务 |
| HANDOVER | 任务交接 |

#### 反馈评价

| 类型 | 用途 |
|------|------|
| FEEDBACK | 对单次任务的反馈 |
| REVIEW | 绩效评价（写入简历） |

#### 提醒通知

| 类型 | 用途 |
|------|------|
| NOTIFICATION | 通用通知（不需要回复） |
| REMINDER | 提醒（任务快到期、等待回复太久） |
| ALERT | 警报（紧急情况需要立即关注） |

#### 监察

| 类型 | 用途 |
|------|------|
| AUDIT_REQUEST | 监察 Agent 要求查看记录/状态 |
| AUDIT_REPORT | 监察结果报告 |

#### 权限

| 类型 | 用途 |
|------|------|
| GRANT_PERMISSION | 授予权限（如许愿权限下放） |
| REVOKE_PERMISSION | 撤销权限 |

#### 系统

| 类型 | 用途 |
|------|------|
| ACK | 确认收到 |
| PING | 检查对方是否在线 |
| PONG | 回应 PING |

### 消息持久化

- 所有消息都存储
- 有 expire 机制（暂时设为无限）

---

## 第四部分：消息内容结构（Content）

消息的 content 字段根据 type 不同而有不同的结构。
采用混合格式：结构化字段 + 自然语言字段。

### TASK

```yaml
content:
  task_id: string
  title: string
  description: string         # 自然语言描述
  requirements:
    output_format: string
    constraints: list
  priority: enum              # HIGH / MEDIUM / LOW
  deadline: datetime
  resources:
    tools: list
    budget: object
    subordinates: list
  acceptance_criteria: string
```

### REPORT

```yaml
content:
  task_id: string
  report_type: enum           # PROGRESS / COMPLETE / BLOCKED / FAILED
  progress:
    percentage: int
    current_step: string
  result:
    summary: string
    deliverables: list
  issues:
    blockers: list
    risks: list
  requests:
    need_help: bool
    need_resources: list
    need_clarification: string
```

### ESCALATE

```yaml
content:
  task_id: string
  reason: enum                # CANNOT_SOLVE / NEED_DECISION / OUT_OF_SCOPE / RESOURCE_NEEDED
  description: string
  attempts: list              # 已尝试的方法
  suggestion: string
```

### QUERY

```yaml
content:
  question: string
  query_type: enum            # INFO / CLARIFICATION / PERMISSION / ADVICE
  urgency: enum               # URGENT / NORMAL / LOW
  options: list
```

### MEETING_START

```yaml
content:
  meeting_id: string
  topic: string
  agenda: list
  participants: list
  decision_mode: enum         # LEADER_DECIDE / VOTE
```

### INTRODUCE

```yaml
content:
  new_contact: string         # 新认识的节点ID
  relationship: enum          # PEER / SUBORDINATE / COLLABORATOR
  capabilities: list
  context: string             # 为什么介绍你们认识
```

### REQUEST_HIRE

```yaml
content:
  requester: string
  requirements:
    role: string
    capabilities: list
    personality: string
  urgency: enum
  budget: object
```

---

## 第五部分：人才池与招募

### 简历（Resume）

每个可招募的 Agent（无论内部还是外部）都有一份简历。
Agent 通过简历被认识和筛选。

```yaml
Resume:
  id: string
  name: string

  # 基本信息
  type: enum                  # LOCAL / EXTERNAL / TEMPLATE
  source: string              # 来自哪个人才市场/池

  # 能力
  capabilities: list
  specialties: list
  tools: list

  # 画像
  personality: string
  work_style: string
  communication: string

  # 经验（内部 Agent 才有）
  experience:
    tasks_completed: int
    success_rate: float
    past_roles: list
    reviews: list             # 上级评价

  # 状态
  availability: enum          # AVAILABLE / EMPLOYED / SUSPENDED
  current_employer: string

  # 成本（外部 Agent）
  pricing:
    model: string             # "per_call" / "per_token" / "free"
    rate: float
```

### 人才池结构

```yaml
TalentPool:
  internal:                   # 内部人才池（初始为空）
    - Agent 实例（有记忆、有经验、待业中）

  external:                   # 外部人才市场（通过网络接入，可配多个市场）
    - market_id: string
      name: string            # "OpenAI Assistants" / "Ollama" / ...
      adapter: string
      catalog: list           # 静态配置 + 动态查询 都支持
```

### 招募机制

基础设施提供：
- 人才池的存储和查询接口
- 简历的存储和匹配接口
- 从外部市场获取简历的适配器接口

具体的招募流程（筛选方式、面试流程、评估标准）由 HR Agent 自行决定，
通过其 Skill 来驱动。

---

## 第六部分：外部 Agent 接入

### 适配器节点（Adapter Node）

外部 Agent 不理解我们的协议，需要通过适配器接入网络。
适配器是我们网络中的一个本地节点，它负责：

- 维护上下级关系（外部 Agent 本身不知道有层级）
- 翻译消息格式（将内部协议转换为外部 API 调用）
- 管理上下文/记忆（为无状态的外部 Agent 维护本地状态）

```yaml
AdapterNode:
  id: string
  type: EXTERNAL_AGENT

  external_config:
    service_type: string      # "openai" / "claude" / "ollama" / ...
    connection: object        # API key、endpoint 等
    model: string

  local_state:
    memory: object            # 本地维护的记忆
    context_window: list      # 发送给外部 Agent 的上下文

  relation_aware: true        # 适配器知道自己在组织中的位置
```

### 外部 Agent 的下级

如果外部 Agent 也需要下级：
- 适配器暴露工具（tool）给外部 Agent
- 工具之一是 "delegate_to_subordinate"
- 外部 Agent 调用此工具时，适配器将任务转发给下级节点

---

## 第七部分：特殊角色

**这些都是普通节点，只是扮演特殊角色。没有系统级特殊待遇。**
它们的行为由各自的 Skill 驱动。

### Agent God

- 纯工厂角色，只负责创造新 Agent
- 不直接接受上级许愿，而是通过 HR 调用
- 创造时可为新 Agent 注入初始 Skill

### HR Agent

- 负责人力资源
- 接收招募请求，从人才池筛选/招募（通过简历）
- 必要时调用 Agent God 创造新 Agent
- 管理配额
- 具体的招募流程/面试方式/评估标准由其 Skill 决定

### 监察 Agent

- 负责审计其他 Agent 的行为
- 触发机制：定期审计、事件触发（失败/超时/异常）、上级请求

---

## 第八部分：设计决策汇总

### 基础设施层决策

| 方面 | 决定 |
|------|------|
| 持久化 | 文件存储（上下文 + 状态） |
| 记忆 | 每个 Agent 独立记忆 |
| 通信方式 | 异步 |
| 内外部消息 | 区分（内部有 ACK，外部等回复） |
| 消息格式 | 混合（结构化 + 自然语言） |
| 消息持久化 | 全部存储，expire 机制（暂无限） |
| 关系结构 | 严格树状，单上级，不允许兼职/循环 |
| 关系配置 | 绑定在关系上（非节点） |
| 人才池 | 内部（初始空）+ 外部（可配多市场） |
| 外部市场简历 | 静态配置 + 动态查询 |

### 可配置项（通过关系配置或节点配置）

| 方面 | 说明 |
|------|------|
| 命令/协商模式 | 绑定在关系上 |
| 决策机制 | 拍板 / 投票，绑定在关系上 |
| 越级汇报 | 绑定在关系上 |
| 资源配额 | 有限 / 无限 |
| 许愿权限 | 可下放 |

### Agent 层决策（由 Agent 自行通过 Skill 实现）

| 方面 | 说明 |
|------|------|
| 团队组织方式 | Agent 自己决定 |
| 招募/面试流程 | HR Agent 的 Skill |
| 入职流程 | 由上级或 HR 的 Skill 决定 |
| SOP / 工作流 | Agent 的 Skill |
| 任务分解策略 | Agent 自己决定 |
| 进度追踪方式 | 主动下查 + 下级汇报（协议支持，具体策略由 Agent 决定） |
| 失败处理 | 无法解决则上报（协议支持 ESCALATE，具体策略由 Agent 决定） |

---

## 第九部分：技术选型（待定）

| 方面 | 候选 |
|------|------|
| Agent 框架 | Google ADK（待评估） |
| Agent 间通信 | 异步 |
| LLM | 可配置多种（通过 LiteLLM 或类似方案） |

---

## 架构图

```
                    ┌─────────────┐
                    │   Tyrant    │  (网络所有者，人类)
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │  Top Agent  │  (普通节点)
                    └──────┬──────┘
            ┌──────────────┼──────────────┐
            │              │              │
     ┌──────┴──────┐ ┌─────┴─────┐ ┌──────┴──────┐
     │ Worker      │ │ HR Agent  │ │  Adapter    │
     │ (本地Agent) │ │           │ │ (外部Agent) │
     └─────────────┘ └─────┬─────┘ └─────────────┘
                           │
              ┌────────────┴────────────┐
              │       Talent Pool       │
              │  ┌───────────────────┐  │
              │  │     简历库         │  │
              │  │ - 内部 Agent 简历  │  │
              │  │ - 外部市场简历     │  │
              │  └───────────────────┘  │
              └────────────┬────────────┘
              ┌────────────┴────────────┐
              │                         │
       ┌──────┴──────┐          ┌───────┴───────┐
       │ Agent God   │          │ 外部人才市场   │
       │ (工厂)      │          │ (OpenAI 等)   │
       └─────────────┘          └───────────────┘
```

---

## 提议（暂不实现）

- Agent 晋升机制（下级通过经验升级为上级）
- 先知 Agent（任务规划顾问 / 风险预警员 / 模式学习者）
- 内部人才池模板
- Agent 克隆（复制优秀 Agent 的配置和 Skill）
- 经验合并（把 A 的经验给 B）
- ROI 评估（成本管理）

---

## 典型场景

**场景：Tyrant 让 Top Agent "写一个爬虫项目"**

1. Top Agent 分析任务，发现需要 "需求分析"、"代码编写"、"测试"
2. Top Agent 向 HR 发送 REQUEST_HIRE
3. HR 查看人才池简历，筛选合适的候选人
4. HR 招募（激活内部 Agent / 创建外部适配器 / 向 Agent God 申请创造）
5. HR 向 Top Agent 发送 INTRODUCE，介绍新成员
6. Top Agent 向新成员发送 TASK
7. 执行任务，通过 REPORT 汇报进度
8. 完成后 Top Agent 向 Tyrant 汇报结果

---

## 待讨论

1. 消息类型是否还有遗漏？
2. 适配器的智能程度 — 只翻译？还是也做决策（如判断是否拆分任务）？
3. 技术选型最终确定
