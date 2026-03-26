# TyrantCore v2 执行计划

> 状态：核心重构已完成
> 日期：2026-03-26

本文用于把 v2 重构从“设计稿”推进到“可执行工程计划”。

---

## 目标

本次重构按以下原则执行：

- 旧实现迁入 `src/tyrant_core/legacy/`，作为参考实现保留
- 新实现继续使用 `src/tyrant_core/` 原路径
- 不以兼容 v1 为目标
- 优先建立 v2 骨架与边界，再逐步填充能力

---

## Phase 0：冻结 v1 并保留参考

### 目标

- 把当前 v1 代码复制到 `src/tyrant_core/legacy/`
- 让 `legacy` 形成独立参考区
- 明确后续实现默认不再修改 legacy 逻辑

### 清单

- [x] 创建 `src/tyrant_core/legacy/`
- [x] 复制当前 v1 模块到 `legacy/`
- [x] 修正 `legacy` 内部 import 路径
- [x] 新增 `legacy/README.md`

---

## Phase 1：建立 v2 主骨架

### 目标

- 建立新的模块布局
- 为 v2 的核心对象和模块边界占位
- 让后续开发围绕新骨架推进

### 清单

- [x] 新建 `gateway/`
- [x] 新建 `api/command_api.py`
- [x] 新建 `api/query_api.py`
- [x] 新建 `bus/event_stream.py`
- [x] 新建 `bus/subscription_hub.py`
- [x] 新建 `runtime/execution_manager.py`
- [x] 新建 `runtime/traces.py`
- [x] 更新各包 `__init__.py`

---

## Phase 2：定义 v2 最小领域模型

### 目标

- 建立最关键的对象
- 让命令、查询、gateway、runtime 有统一语言

### 清单

- [x] 定义 `Actor`
- [x] 定义 `Grant`
- [x] 定义 `Thread`
- [x] 定义 `Envelope`
- [x] 定义 `Delivery`
- [x] 定义 `WorkItem`

说明：

- 这一阶段会正式替换 `src/tyrant_core/models.py`
- 已同步替换活跃命名空间中的 `store/api/bus/runtime` 默认实现，使其不再隐式依赖 v1 模型

---

## Phase 3：建立 Gateway 最小闭环

### 目标

- 用 delivery lease/ack 替代 v1 inbox queue 语义
- 建立本地 runtime 与 gateway 的新边界

### 清单

- [x] 定义 gateway 协议对象
- [x] 实现 `BindingResolver`
- [x] 实现 `SessionRegistry`
- [x] 实现 `DeliveryEngine`
- [x] 提供 embedded CLI connector
- [x] 提供 local runtime consumer

---

## Phase 4：切换入口与运行路径

### 目标

- 新入口默认走 v2
- legacy 入口仅用于参考/对照/必要时回滚验证

### 清单

- [x] 重写 `src/tyrant_core/__main__.py`
- [x] 提供 `legacy` 独立启动入口
- [x] 更新 README 和架构文档
- [x] 标记旧测试为 legacy 范围

---

## Phase 4.5：把骨架替换成真实控制平面

### 目标

- 去掉内存过渡版 store
- 让 session / binding / delivery 具备真实持久化与重启恢复能力

### 清单

- [x] 用真实 SQLite 实现替换 v2 `SqliteStore`
- [x] 新表结构对齐 v2 领域模型
- [x] 让 `SessionRegistry` 基于 store 持久化
- [x] 让 `BindingResolver` 基于 store 持久化
- [x] 打通 gateway 重启后的 session / delivery 恢复
- [x] 新增 v2 store / recovery 测试

---

## Phase 4.6：清理活跃路径残留 v1 适配器

### 目标

- 让 `src/tyrant_core/` 主路径彻底不再依赖 v1 `Node / MessageBus`
- 让 v2 核心对象不只是 schema，而是可操作的 API 边界

### 清单

- [x] 重写活跃 `adapters/cli.py`
- [x] 重写活跃 `adapters/terminal_node.py`
- [x] 补齐 `OrgEdge / Grant / Attachment / Presence` API
- [x] 实现 store-backed `PolicyEngine`
- [x] 新增活跃 adapter / core api 测试

---

## Phase 4.7：提纯 websocket 接入层

### 目标

- 让 websocket 不再是单类混合“连接状态 + 路由决策 + 传输实现”
- 为未来真实网络 server/transport 壳层预留清晰插口
- 明确多连接 egress 的默认语义

### 清单

- [x] 拆出 `WebSocketConnectionRegistry`
- [x] 拆出 `WebSocketDeliveryRouter`
- [x] 拆出 `WebSocketTransport` 与默认 `InMemoryWebSocketTransport`
- [x] 把 `WebSocketConnector` 收口为编排层
- [x] 补齐多连接 `PREFER_REQUESTED / BROADCAST` 测试

---

## Phase 4.8：补真实 websocket 网络壳层

### 目标

- 让 v2 的 websocket 不只停留在内存 transport/operator
- 在不污染 gateway 领域层的前提下，补一层真实 socket server 壳层
- 给 `GatewayServer` 提供统一注册入口

### 清单

- [x] 新增 `DirectWebSocketTransport`
- [x] 新增 `WebSocketServerShell`
- [x] 新增 `GatewayServer.register_websocket_server()`
- [x] 补 fake serve/fake socket 单测
- [x] 跑通真实 loopback websocket smoke

---

## 本次执行范围

本轮直接执行以下事项：

- [x] 完成 Phase 0
- [x] 完成 Phase 1
- [x] 完成 Phase 2
- [x] 完成 Phase 3
- [x] 完成 Phase 4
- [x] 完成 Phase 4.5
- [x] 完成 Phase 4.6
- [x] 完成 Phase 4.7
- [x] 完成 Phase 4.8
- [x] 更新相关文档入口
- [x] 做最小验证

---

## 当前结论

- 仓库默认真相已切到“v1 参考区 + v2 活跃实现”
- v2 当前最小可用闭环已具备真实持久化，不再依赖进程内内存字典
- v2 活跃路径已清理掉对旧 `Node / MessageBus` 的默认依赖
- websocket 接入层已经提纯成“连接表 + 路由策略 + 传输壳层 + connector 编排 + 网络 shell”，并已验证真实 loopback socket 闭环
- 旧测试仍大多属于 legacy 范围，是否迁移为 v2 测试属于后续清理工作，不再阻塞本轮核心重构完成
