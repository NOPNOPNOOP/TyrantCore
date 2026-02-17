# bus/message_bus.py — 消息总线

依赖：store, models, event_bus

## 职责

- 验证消息路由权限（子树可达模型）
- 填充 `from_node`（不可伪造）
- 多播展开（list → 多条单发）
- 投递到接收者 inbox
- ACK 管理

## 接口

- `send(message, sender_id) → list[str]` — 路由检查 + 投递，返回 message_id 列表
- `get_inbox(node_id, limit) → list[Message]` — 按 priority ASC 排序
- `ack(message_id, node_id)` — 确认消息已处理
- `subscribe_inbox(node_id) → AsyncIterator[Message]` — 实时监听（Agent runtime 用）

## 路由检查

子树可达模型：from 和 to 在同一子树内即可通信。系统消息始终允许。

## Inbox 机制

- 内存 `dict[str, asyncio.Queue[Message]]`
- 新消息：先持久化 SQLite → 再 put 到内存 queue
- 系统重启时从 SQLite 恢复未 ACK 的消息
