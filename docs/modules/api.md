# api/system_api.py — 系统 API

> 注意：本文描述的是 v1 / legacy API 形态，当前活跃实现请优先阅读 [`docs/architecture-v2.md`](../architecture-v2.md) 与 [`docs/v2-execution-plan.md`](../v2-execution-plan.md)。

依赖：store, message_bus, event_bus, models

## 职责

所有对外操作的唯一入口，权限检查在此层完成。

## 接口

### 节点生命周期

- `create_agent(caller_id, name, parent_id, agent_config?) → Node` — 原子：创建节点 + REPORTS_TO + CONNECTED
- `destroy_agent(caller_id, target_id)` — 销毁节点
- `clone_node(caller_id, source_id, parent_id, name?) → Node` — 复制 agent_config + permissions + tags

### 人才池

- `talent_pool_release(caller_id, target_id)` — 释放到人才池
- `talent_pool_recruit(caller_id, target_id, parent_id)` — 从人才池招募
- `talent_pool_query(caller_id) → list[Node]`

### 权限

- `grant_permission(caller_id, target_id, permission)`
- `revoke_permission(caller_id, target_id, action)`
- `create_delegation(caller_id, delegate_id, action, scope, one_time?) → Delegation`
- `revoke_delegation(caller_id, delegation_id)`

### 消息

- `send_message(caller_id, message) → list[str]`
- `query_messages(caller_id, node_id?, from_node?, to_node?, type?, limit?) → list[Message]`

### 任务

- `task_create(caller_id, task) → Task` — 完成时自动通知 creator
- `task_update(caller_id, task_id, status?, content?, result?) → Task`
- `task_query(caller_id, **filters) → list[Task]`

### 查询

- `list_nodes(caller_id, tags?, type?, status?) → list[Node]` — 服务发现
- `query_conversation_history(caller_id, target_id, trigger_message_id?, limit?) → list[ConversationTurn]`
- `audit_inspect(caller_id, target_id) → dict`

## 权限检查

`_check_permission(caller_id, action, target_id)`:
1. Tyrant (is_owner) → 放行
2. 直接上级对下属 → 隐含 audit
3. 收集自身 permissions + delegations → deny 优先 → allow → 默认拒绝
