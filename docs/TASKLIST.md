# TyrantCore 实现任务追踪

实现顺序遵循模块依赖图：models → store → bus → api → runtime → cli

## 状态说明

- ⬜ 未开始
- 🔧 进行中
- ✅ 已完成
- 🧪 已完成 + 测试通过

## 任务列表

### 1. 项目脚手架
- ✅ pyproject.toml 配置（依赖、构建、pytest）
- ✅ src/tyrant_core/ 目录结构 + __init__.py
- ✅ tests/ 目录

### 2. models.py — [spec 01](specs/01-models.md)
- ✅ Enums: NodeType, NodeStatus, RelationType, TaskStatus, Priority, RefType
- ✅ 嵌套结构: Endpoint, Permissions, Reference
- ✅ 核心实体: Node, Relation, Message, Task, Event
- ✅ 异常: TyrantError 及子类
- 🧪 测试: tests/test_models.py (20 passed)

### 3. store/ — [spec 02](specs/02-store.md)
- ✅ base.py: Store ABC 抽象接口
- ✅ sqlite_store.py: 7 个 Core Table 定义
- ✅ sqlite_store.py: SqliteStore 完整 CRUD 实现
- ✅ sqlite_store.py: 事务管理 (transaction context manager)
- 🧪 测试: tests/test_store.py (22 passed)

### 4. bus/event_bus.py — [spec 04](specs/04-event-bus.md)
- ✅ 内存订阅注册表 (dict pattern → callbacks)
- ✅ 通配符匹配 (_matches)
- ✅ publish: 持久化 + 分发
- ✅ subscribe / unsubscribe
- ✅ replay: 历史回放
- ✅ recover_subscriptions: 重启恢复
- 🧪 测试: tests/test_event_bus.py (11 passed)

### 5. bus/message_bus.py — [spec 03](specs/03-message-bus.md)
- ✅ 路由检查 (_check_route)
- ✅ from_node 自动填充
- ✅ 多播展开 (list → 多条单发)
- ✅ 内存 asyncio.Queue inbox
- ✅ send / get_inbox / ack / subscribe_inbox
- 🧪 测试: tests/test_message_bus.py (11 passed)

### 6. api/system_api.py — [spec 05](specs/05-api.md)
- ✅ 权限检查框架
- ✅ Node 生命周期 (create_agent / destroy_agent)
- ✅ 人才池管理 (recruit / release)
- ✅ 关系管理
- ✅ 任务 CRUD
- ✅ 消息发送
- 🧪 测试: tests/test_system_api.py (11 passed)

### 7. runtime/agent_runtime.py — [spec 06](specs/06-runtime.md)
- ✅ ADK LlmAgent 配置
- ✅ SystemAPI → ADK tools 映射 (8 个 tools)
- ✅ inbox 桥接 (Queue → ADK)
- ✅ Runner.run_async() 集成

### 8. adapters/cli.py — [spec 07](specs/07-cli-adapter.md)
- ✅ prompt_toolkit 异步输入
- ✅ rich 输出格式化
- ✅ 命令解析 (12 个命令)
- ✅ Tyrant 节点初始化

### 9. __main__.py + 集成验证
- ✅ 启动流程组装 (Store → Bus → API → Runtime → CLI)
- ✅ 模块导入验证通过
- 🧪 全部测试: 75 passed

## 待办

### 11. 非交互式 CLI（命令行协作接口）
- ⬜ 子命令模式（`tyrant-core nodes list`, `tyrant-core message send` 等）
- ⬜ JSON 输出格式（方便外部 agent 系统解析）
- ⬜ 身份认证（--as <node_id> 指定操作身份）
- ⬜ 后续考虑 HTTP API 协作方式

### 12. 子树可达路由
- ✅ 路由规则从“直接关系”扩展为“子树内可达”（与权限模型保持一致）
- ✅ Tyrant 子树 = 整个网络，天然全通
- 🧪 测试通过: 75 passed + 手动验证 Tyrant 可达 manager 的下属

### 10. config.py — 配置系统
- ✅ pydantic-settings Settings 类 (ASA_* 前缀)
- ✅ ~/.config/tyrant_core/.env 配置文件
- ✅ LiteLLM 模型适配 (openai/ 前缀自动添加)
- ✅ apply_env() 环境变量注入
- ✅ __main__.py 集成 Settings
- ✅ runtime 接受 Settings 参数
- ✅ pyproject.toml 添加 litellm 依赖
- 🧪 全部测试: 75 passed
