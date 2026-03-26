# bus/event_bus.py — 事件总线

> 注意：本文描述的是 v1 / legacy 事件总线文档，当前活跃实现请优先阅读 [`docs/architecture-v2.md`](../architecture-v2.md) 与 [`docs/v2-execution-plan.md`](../v2-execution-plan.md)。

依赖：store, models

## 职责

- 事件发布（系统事件 + Agent 自定义事件）
- 订阅管理（通配符匹配）
- Append-only 日志持久化
- 历史回放

## 接口

- `publish(event)` — 持久化 + 分发到匹配订阅者
- `subscribe(node_id, event_type, callback)` — 支持通配符（`node.*`）
- `unsubscribe(node_id, event_type)`
- `replay(since, types) → list[Event]` — 回放历史事件
- `recover_subscriptions()` — 重启恢复

## 内存结构

```python
self._subscribers: dict[str, list[tuple[str, Callable]]]
# event_type_pattern → [(node_id, callback)]
```

## 权限控制

EventBus 不做权限检查，信任 SystemAPI 层已验证。
