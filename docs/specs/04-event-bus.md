# 04 - 事件总线 (bus/event_bus.py)

依赖：store, models

## 职责

- 事件发布（系统事件 + Agent 自定义事件）
- 订阅管理（节点订阅事件类型）
- 事件分发到订阅者的回调
- Append-only 日志持久化
- 历史回放（从指定时间点消费）

## 接口

```python
class EventBus:
    def __init__(self, store: Store): ...

    async def publish(self, event: Event) -> None:
        """
        发布事件。
        - 持久化到事件日志
        - 分发到匹配的订阅者回调
        """

    async def subscribe(self, node_id: str, event_type: str, callback: Callable) -> None:
        """
        订阅事件类型。
        - event_type 支持通配符: "node.*" 匹配 "node.status_changed" 等
        - callback: async def handler(event: Event) -> None
        - 持久化订阅关系到 store
        """

    async def unsubscribe(self, node_id: str, event_type: str) -> None:
        """取消订阅"""

    async def replay(self, since: datetime, types: list[str] | None = None) -> list[Event]:
        """回放指定时间点之后的事件"""

    async def recover_subscriptions(self) -> None:
        """系统重启时从 store 恢复订阅关系（回调需由 runtime 重新注册）"""
```

## 内存结构

```python
# 订阅注册表：event_type_pattern → [(node_id, callback)]
self._subscribers: dict[str, list[tuple[str, Callable]]] = {}
```

## 事件匹配

```python
def _matches(self, pattern: str, event_type: str) -> bool:
    """
    简单通配符匹配：
    - "node.*" 匹配 "node.status_changed", "node.destroyed"
    - "task.status_changed" 精确匹配
    - "*" 匹配所有
    """
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return event_type.startswith(prefix + ".")
    return pattern == event_type
```

## 发布流程

```python
async def publish(self, event: Event) -> None:
    # 1. 持久化
    await self.store.append_event(event)

    # 2. 分发到匹配的内存订阅者
    for pattern, subscribers in self._subscribers.items():
        if self._matches(pattern, event.type):
            for node_id, callback in subscribers:
                try:
                    await callback(event)
                except Exception:
                    logger.exception(f"Event callback failed: {node_id} {event.type}")
```

## 权限控制

订阅的权限检查不在 EventBus 中，而在 SystemAPI 层：
- 节点只能订阅子树内节点的事件（source 在自己子树中）
- 有 audit 权限的节点可以订阅子树外的事件
- EventBus 本身不做权限检查，信任上层已验证

## 回放

Agent 重启后调用 `replay(since=last_seen_timestamp)` 获取错过的事件。
`last_seen_timestamp` 由 Agent runtime 维护（持久化在 agent state 中）。
