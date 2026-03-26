# models.py — 数据模型

> 注意：本文描述的是 v1 / legacy 数据模型，当前活跃模型请以 [`docs/architecture-v2.md`](../architecture-v2.md) 和 `src/tyrant_core/models.py` 为准。

零依赖模块，纯 Pydantic 模型 + Enum + 异常。

## Enums

| Enum | 值 |
|------|------|
| NodeType | HUMAN / LOCAL_AGENT / EXTERNAL_AGENT / TERMINAL |
| NodeStatus | ONLINE / BUSY / OFFLINE / SUSPENDED |
| RelationType | REPORTS_TO / CONNECTED |
| TaskStatus | PENDING / IN_PROGRESS / BLOCKED / COMPLETED / FAILED |
| Priority | SYSTEM(0) / URGENT(1) / NORMAL(2) / LOW(3) |
| RefType | MESSAGE / TASK / NODE |
| ScopeType | SELF_SUBTREE / GLOBAL / SUBTREES / NODES |
| PermissionEffect | ALLOW / DENY |

## 嵌套结构

| 模型 | 字段 |
|------|------|
| Endpoint | protocol, address, config={} |
| Reference | ref_type, ref_id, relation |
| Scope | type: ScopeType, targets: list[str] |
| Permission | action, effect=ALLOW, scope |

## 核心实体

详见 [architecture.md](../architecture.md#核心实体)。

## 异常

```
TyrantError
  ├── NodeNotFoundError
  ├── RelationNotFoundError
  ├── MessageNotFoundError
  ├── TaskNotFoundError
  ├── PermissionDeniedError
  ├── RoutingError
  └── ValidationError
```

## 约定

- `Message.from_node` 避开 Python `from` 保留字，序列化时映射为 `"from"`
- `Message.to` 支持 `str | list[str]`，消息总线投递前展开
- 所有 ID: uuid4 字符串
- 时间: UTC datetime，存储为 ISO 8601 字符串
