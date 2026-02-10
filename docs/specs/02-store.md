# 02 - 持久化层 (store/)

依赖：models, sqlalchemy, aiosqlite

## 抽象接口 (base.py)

```python
class Store(ABC):
    # Node
    async def save_node(self, node: Node) -> None
    async def get_node(self, node_id: str) -> Node | None
    async def update_node(self, node_id: str, **fields) -> None
    async def delete_node(self, node_id: str) -> None
    async def list_nodes(self, **filters) -> list[Node]

    # Relation
    async def save_relation(self, relation: Relation) -> None
    async def get_relation(self, relation_id: str) -> Relation | None
    async def delete_relation(self, relation_id: str) -> None
    async def get_relations_for_node(self, node_id: str) -> list[Relation]
    async def get_reports_to(self, node_id: str) -> Relation | None  # 获取该节点的上级关系
    async def get_subordinates(self, node_id: str) -> list[Relation]  # 获取直接下属
    async def get_subtree_node_ids(self, node_id: str) -> set[str]   # 获取整棵子树

    # Message
    async def save_message(self, message: Message) -> None
    async def get_message(self, message_id: str) -> Message | None
    async def get_inbox(self, node_id: str, limit: int = 50) -> list[Message]  # 按 priority 排序
    async def mark_delivered(self, message_id: str, node_id: str) -> None
    async def mark_acked(self, message_id: str, node_id: str) -> None

    # Task
    async def save_task(self, task: Task) -> None
    async def get_task(self, task_id: str) -> Task | None
    async def update_task(self, task_id: str, **fields) -> None
    async def query_tasks(self, **filters) -> list[Task]

    # Event
    async def append_event(self, event: Event) -> None
    async def get_events(self, since: datetime, types: list[str] | None = None, limit: int = 100) -> list[Event]

    # Transaction
    def transaction(self) -> AsyncContextManager  # async with store.transaction(): ...

    # Lifecycle
    async def initialize(self) -> None   # 创建表、索引等
    async def close(self) -> None
```

## SQLAlchemy 模型定义 (sqlite_store.py)

使用 SQLAlchemy 2.0 async + aiosqlite 驱动。采用 Core Table 定义（而非 ORM Mapped class），保持轻量。

```python
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Text, DateTime,
    ForeignKey, Index, JSON,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

metadata = MetaData()

nodes_table = Table("nodes", metadata,
    Column("id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("name", String, nullable=False),
    Column("is_owner", Integer, nullable=False, default=0),
    Column("endpoint", JSON, nullable=False),
    Column("status", String, nullable=False, default="OFFLINE"),
    Column("permissions", JSON, nullable=False),
    Column("metadata_", JSON, nullable=False, default={}),  # 避开 SQLAlchemy 保留字
    Column("created_at", String, nullable=False),
)

relations_table = Table("relations", metadata,
    Column("id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("node_a", String, ForeignKey("nodes.id"), nullable=False),
    Column("node_b", String, ForeignKey("nodes.id"), nullable=False),
    Column("config", JSON, nullable=False, default={}),
    Column("created_at", String, nullable=False),
    Index("idx_relations_node_a", "node_a"),
    Index("idx_relations_node_b", "node_b"),
    Index("idx_relations_type", "type"),
)

messages_table = Table("messages", metadata,
    Column("id", String, primary_key=True),
    Column("timestamp", String, nullable=False),
    Column("expires_at", String, nullable=True),
    Column("from_node", String, nullable=False),
    Column("to_node", String, nullable=False),
    Column("type", String, nullable=False, default="CHAT"),
    Column("priority", Integer, nullable=False, default=2),
    Column("content", JSON, nullable=False, default={}),
    Column("references_json", JSON, nullable=False, default=[]),
    Column("thread_id", String, nullable=True),
    Column("requires_ack", Integer, nullable=False, default=0),
    Index("idx_messages_to", "to_node"),
    Index("idx_messages_thread", "thread_id"),
)

deliveries_table = Table("deliveries", metadata,
    Column("message_id", String, ForeignKey("messages.id"), primary_key=True),
    Column("node_id", String, ForeignKey("nodes.id"), primary_key=True),
    Column("delivered_at", String, nullable=True),
    Column("acked_at", String, nullable=True),
)

tasks_table = Table("tasks", metadata,
    Column("id", String, primary_key=True),
    Column("creator", String, nullable=False),
    Column("assignee", String, nullable=False),
    Column("participants", JSON, nullable=False, default=[]),
    Column("status", String, nullable=False, default="PENDING"),
    Column("content", JSON, nullable=False, default={}),
    Column("parent_task", String, nullable=True),
    Column("references_json", JSON, nullable=False, default=[]),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("idx_tasks_assignee", "assignee"),
    Index("idx_tasks_status", "status"),
)

events_table = Table("events", metadata,
    Column("id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("source", String, nullable=False),
    Column("data", JSON, nullable=False, default={}),
    Index("idx_events_timestamp", "timestamp"),
    Index("idx_events_type", "type"),
)

subscriptions_table = Table("subscriptions", metadata,
    Column("node_id", String, ForeignKey("nodes.id"), primary_key=True),
    Column("event_type", String, primary_key=True),
    Column("created_at", String, nullable=False),
)
```

## SqliteStore 实现

```python
class SqliteStore(Store):
    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()
```

## Pydantic ↔ SQLAlchemy 转换约定

- Pydantic 模型与数据库行的转换在 SqliteStore 内部完成
- JSON 字段（dict, list）由 SQLAlchemy `JSON` 类型自动处理序列化/反序列化
- `datetime` 存为 ISO 8601 字符串
- `bool` 存为 0/1

## 事务

```python
async with store.transaction():
    await store.save_node(node)
    await store.save_relation(relation)
    # 任何一步失败则全部回滚
```

内部实现基于 SQLAlchemy async session 的 `begin()` 事务：

```python
@asynccontextmanager
async def transaction(self):
    async with self._session_factory() as session:
        async with session.begin():
            self._current_session = session
            yield
        self._current_session = None
```

SQLite 单连接天然串行化，aiosqlite 在后台线程执行。v1 无需额外的并发控制。

## 文件存储

Agent 记忆等大 blob 数据存在文件系统中：

```
data/
  tyrant.db              # SQLite 主数据库
  agents/
    {node_id}/
      memory.json        # Agent 记忆
      state.json         # Agent 持久状态
```

文件路径通过 Store 配置的 `data_dir` 确定。文件读写不在 Store 接口中，由 runtime 层直接管理。
