# 03 - 消息总线 (bus/message_bus.py)

依赖：store, models, event_bus

## 职责

- 验证消息路由权限
- 填充 `from_node`（不可伪造）
- 多播展开（list → 多条单发）
- 投递到接收者 inbox
- ACK 管理
- 过期消息清理

## 接口

```python
class MessageBus:
    def __init__(self, store: Store, event_bus: EventBus): ...

    async def send(self, message: Message, sender_id: str) -> list[str]:
        """
        发送消息。
        - 系统自动填充 from_node = sender_id
        - 检查路由权限
        - 多播展开
        - 投递到每个接收者的 inbox
        - 返回实际投递的 message_id 列表
        """

    async def get_inbox(self, node_id: str, limit: int = 50) -> list[Message]:
        """获取节点的待处理消息，按 priority ASC, timestamp ASC 排序"""

    async def ack(self, message_id: str, node_id: str) -> None:
        """确认消息已处理"""

    async def subscribe_inbox(self, node_id: str) -> AsyncIterator[Message]:
        """异步迭代器，实时监听新消息（用于 Agent runtime）"""

    # 内部方法
    async def _check_route(self, from_id: str, to_id: str) -> bool
    async def _deliver(self, message: Message, to_id: str) -> None
```

## 路由检查逻辑

```python
async def _check_route(self, from_id: str, to_id: str) -> bool:
    # 1. 系统消息 → 始终允许
    if from_id == "system":
        return True

    # 2. 查找两者之间的直接关系
    relations = await self.store.get_relations_for_node(from_id)
    for rel in relations:
        other = rel.node_b if rel.node_a == from_id else rel.node_a
        if other == to_id:
            return True  # REPORTS_TO 或 CONNECTED 都允许

    return False
```

## Inbox 机制

- 内存中每个节点维护一个 `asyncio.Queue`
- 新消息到达时：先持久化到 SQLite → 再 put 到内存 queue
- `subscribe_inbox` 从 queue 中 get，阻塞等待
- 系统重启时从 SQLite 恢复未 ACK 的消息到 queue

```python
# 内存结构
self._inboxes: dict[str, asyncio.Queue[Message]] = {}

async def _deliver(self, message: Message, to_id: str) -> None:
    await self.store.save_message(message)
    await self.store.mark_delivered(message.id, to_id)

    if to_id not in self._inboxes:
        self._inboxes[to_id] = asyncio.Queue()
    await self._inboxes[to_id].put(message)
```

## ACK 管理

- `requires_ack=True` 的消息需要接收方显式调用 `ack()`
- v1 暂不实现超时重发（简化）。未 ACK 的消息在系统重启时重新投递。

## 多播展开

```python
# send() 中
if isinstance(message.to, list):
    for to_id in message.to:
        single = message.model_copy(update={"to": to_id, "id": new_uuid()})
        await self._deliver(single, to_id)
else:
    await self._deliver(message, message.to)
```

## 过期清理

- 可选的后台任务，定期删除 `expires_at < now` 的消息
- v1 可以不主动清理，只在 `get_inbox` 时过滤掉过期消息
