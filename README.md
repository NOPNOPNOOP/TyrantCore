# TyrantCore

TyrantCore 默认实现已经完成 **v2 核心重构**。

默认活跃实现已经切到 v2：

- 核心对象：`Actor / OrgEdge / Grant / Thread / Envelope / Delivery / Binding / Session / Attachment / Presence / WorkItem`
- 核心模块：`gateway/ + api/ + store/ + runtime/`
- 默认持久化：`SqliteStore -> data/tyrant_core_v2.db`
- Gateway 控制面：`session / binding / delivery` 已使用共享 store 作为系统真相
- 活跃适配器：`adapters/cli.py` 与 `adapters/terminal_node.py` 已重写为 v2 实现
- 主入口：`uv run python -m tyrant_core`

旧的 v1 实现没有删除，但已经整体冻结到 `src/tyrant_core/legacy/`，只作为参考实现保留。

## 当前状态

- `src/tyrant_core/`：v2 活跃命名空间
- `src/tyrant_core/legacy/`：v1 参考区
- `docs/architecture-v2.md`：v2 设计稿
- `docs/v2-execution-plan.md`：v2 执行计划

## 运行

启动 v2：

```bash
uv run python -m tyrant_core
```

启动 legacy v1 参考实现：

```bash
uv run python -m tyrant_core.legacy
```

或：

```bash
uv run python -m tyrant_core.legacy_main
```

## 测试

当前默认验收基线是 v2 新增测试，不是整套 v1 测试。

目前已落地的 v2 测试：

```bash
uv run pytest tests/test_v2_gateway_flow.py -q
uv run pytest tests/test_v2_store.py -q
uv run pytest tests/test_v2_gateway_recovery.py -q
uv run pytest tests/test_v2_core_api.py -q
uv run pytest tests/test_v2_adapters.py -q
```

旧测试大多对应 v1/legacy 结构，后续会逐步迁移或归档。
