# store/ — 持久化层

依赖：models, sqlalchemy, aiosqlite

## 抽象接口 (base.py)

Store ABC 定义所有持久化操作的契约：

- **Node**: save / get / update / delete / list_nodes(**filters)
- **Relation**: save / get / delete / get_relations_for_node / get_reports_to / get_subordinates / get_subtree_node_ids
- **Message**: save / get / get_inbox / mark_delivered / mark_acked / query_messages
- **Task**: save / get / update / query_tasks
- **Delegation**: save / get / delete / get_delegations_for_delegate
- **ConversationTurn**: save / query_conversation_turns
- **Event**: append_event / get_events
- **Transaction**: `async with store.transaction(): ...`
- **Lifecycle**: initialize / close

## SqliteStore 实现

SQLAlchemy 2.0 async + aiosqlite，Core Table 定义（非 ORM Mapped class）。

**表：** nodes, relations, messages, deliveries, tasks, events, subscriptions, delegations, conversation_turns

**转换约定：**
- Pydantic ↔ 数据库行转换在 SqliteStore 内部完成
- JSON 字段由 SQLAlchemy JSON 类型自动序列化
- datetime → ISO 8601 字符串，bool → 0/1
- list_nodes 支持按 type/status/is_owner/tags 过滤（tags 在 Python 层做集合交集）

**事务：** SQLite 单连接串行化，无额外并发控制。
