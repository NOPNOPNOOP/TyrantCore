# tests

当前测试分为两类：

- `tests/test_v2_gateway_flow.py`
  - v2 当前默认验收基线
  - 覆盖最小闭环：CLI connector -> Gateway -> Delivery -> ActorRuntime -> reply
- `tests/test_v2_store.py`
  - v2 SQLite store 验收
  - 覆盖 actor/thread/envelope/session/binding 持久化与 delivery 状态机
- `tests/test_v2_gateway_recovery.py`
  - v2 Gateway 重启恢复验收
  - 覆盖 session 复用、pending/acked delivery 跨进程恢复
- `tests/test_v2_core_api.py`
  - v2 核心对象 API 验收
  - 覆盖 `OrgEdge / Grant / Attachment / Presence / WorkItem / PolicyEngine`
- `tests/test_v2_adapters.py`
  - v2 活跃适配器验收
  - 覆盖 `CliAdapter` 往返与 `TerminalNodeAdapter` 的 terminal I/O bridge
- `tests/test_v2_smoke.py`
  - v2 系统烟测
  - 覆盖 embedded builder、真实 `CliAdapter.run()`、SQLite reopen、后台 provisioning worker、webhook ingress、websocket connection presence、websocket egress、多连接 websocket broadcast
- `tests/test_v2_bus_components.py`
  - v2 connector / 事件件组件测试
  - 现已覆盖 `WebSocketConnectionRegistry / WebSocketDeliveryRouter / InMemoryWebSocketTransport / DirectWebSocketTransport / WebSocketServerShell`
- 其余测试文件
  - 基本都对应 v1 / legacy 结构
  - 目前不作为 v2 默认验收标准
  - 后续会逐步迁移、重写或归档

在 v2 重构阶段，优先运行：

```bash
uv run pytest tests/test_v2_gateway_flow.py tests/test_v2_store.py tests/test_v2_gateway_recovery.py tests/test_v2_core_api.py tests/test_v2_adapters.py -q
uv run pytest tests/test_v2_gateway_flow.py tests/test_v2_store.py tests/test_v2_gateway_recovery.py tests/test_v2_core_api.py tests/test_v2_adapters.py tests/test_v2_smoke.py -q
```

当前还额外定义了测试分层 marker：

- `v2_acceptance`
  - v2 活跃实现的验收/集成测试
  - 当前包含：`test_v2_gateway_flow.py`、`test_v2_gateway_recovery.py`、`test_v2_core_api.py`、`test_v2_adapters.py`、`test_v2_smoke.py`
- `v2_unit`
  - v2 活跃实现的组件/单元测试
  - 当前包含：`test_v2_store.py`、`test_v2_gateway_layers.py`、`test_v2_cli_components.py`、`test_v2_terminal_components.py`
- `legacy`
  - v1 / legacy 参考区测试
  - 不作为当前 v2 默认放行标准
  - 当前其中一部分仍会因为活跃 `models.py` 已切到 v2 而在导入阶段报错，属于已知兼容性债务
- `experimental`
  - 实验性测试
  - 默认不纳入放行门
  - 当前 `test_v5_e2e.py` 仍依赖旧对象模型，属于实验/迁移待办

推荐运行方式：

```bash
# v2 放行门：验收测试
uv run pytest -m v2_acceptance -q

# v2 组件测试
uv run pytest -m v2_unit -q

# v2 覆盖率（活跃实现）
uv run pytest -m "v2_acceptance or v2_unit" --cov=tyrant_core --cov-report=term-missing

# legacy / experimental 单独查看
uv run pytest -m legacy --continue-on-collection-errors -q
uv run pytest -m experimental --continue-on-collection-errors -q
```

注意：

- `pyproject.toml` 已把 `testpaths` 限定为 `tests/`，不会默认收集仓库根目录下的 `test_quick.py`
- marker 通过 `tests/conftest.py` 按文件名自动打标，不需要在每个测试文件里重复声明
- 真实 loopback websocket smoke 目前还没有固化进默认 pytest lane；本轮已经通过一次手工本地端口验证
