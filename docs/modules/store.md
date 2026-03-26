# store/ — v2 持久化层

当前活跃实现是 v2；旧版 store 已迁入 `src/tyrant_core/legacy/store/`。本页描述的是当前默认路径下的持久化设计。

依赖：`models`、`sqlalchemy`、`aiosqlite`

## 模块布局

```text
src/tyrant_core/store/
  __init__.py
  base.py
  sqlite_store.py
  sqlite/
    codec.py
    tables.py
    txn.py
    support.py
    catalog_repo.py
    thread_repo.py
    routing_repo.py
    delivery_repo.py
```

## 抽象接口 (`base.py`)

`Store` 定义 v2 领域对象的持久化契约：

- `Actor`
- `OrgEdge`
- `Grant`
- `Thread`
- `Envelope`
- `Delivery`
- `Binding`
- `Session`
- `Attachment`
- `Presence`
- `WorkItem`
- `claim_work_item()` 用于把待处理工单原子领取为处理中
- `DomainEvent`
- `transaction()`
- `initialize() / close()`

## 当前实现 (`sqlite_store.py`)

`SqliteStore` 现在只承担 façade 职责：

- 组装 `SqliteSessionScope`
- 组装各领域 repo
- 维持对外 `Store` 接口不变
- 保留兼容测试入口：`_dt_to_str / _normalize_value / _row_to_model / _default_database_url`

它不再直接承载全部 SQL 细节。

## `store/sqlite/` 子包职责

### `codec.py`

- datetime 统一转 UTC ISO 8601
- Enum / Pydantic / list / dict 递归规整
- 数据库行与 Pydantic 模型互转

### `tables.py`

- 所有 SQLite 表定义集中在一处
- schema 仍然使用 SQLAlchemy Core Table，而非 ORM 映射类

### `txn.py`

- 管理 engine / session factory
- 维护 `ContextVar` 事务上下文
- 处理 SQLite pragma
- 提供通用 `upsert`

### `catalog_repo.py`

- `Actor / OrgEdge / Grant`

### `thread_repo.py`

- `Thread / Envelope`

### `routing_repo.py`

- `Binding / Session / Attachment / Presence`

### `delivery_repo.py`

- `Delivery / WorkItem / DomainEvent`
- `WorkItem` 的原子领取（`claim_work_item`）也在这里实现

## 设计约定

- JSON 字段由 SQLAlchemy `JSON` 类型自动序列化
- 时间统一存储为 UTC ISO 8601 字符串
- bool 仍按 SQLite 习惯落为 `0/1`
- 所有写操作必须在 `async with store.transaction():` 内执行
- `SqliteSessionScope` 支持嵌套事务语义：内层复用外层 session

## 可测试性

当前设计把测试切成两层：

- `SqliteStore` façade 测试：验证公开接口、事务语义、恢复语义
- `store/sqlite/*` 细分模块测试：通过现有 `v2_unit` / `v2_acceptance` 间接覆盖 repo 与事务实现

这样做的目标是：

- 上层测试只关心 `Store` 契约
- SQL 细节可以按 repo 边界定位
- 后续如果替换 backend，不需要回滚 gateway / api 测试设计
