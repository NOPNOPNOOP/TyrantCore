# 05 - 系统 API (api/system_api.py)

依赖：store, message_bus, event_bus, models

所有对外操作的统一入口。每个方法对应设计文档中的一个系统 API。

## 接口

```python
class SystemAPI:
    def __init__(self, store: Store, message_bus: MessageBus, event_bus: EventBus): ...

    # === Agent 生命周期 ===

    async def create_agent(
        self,
        caller_id: str,         # 调用者节点ID
        name: str,
        parent_id: str,         # 上级节点ID（强制）
        agent_config: dict,     # model, instruction, tools 等 ADK 配置
        metadata: dict = {},
    ) -> Node:
        """
        原子操作：创建节点 + 建立 REPORTS_TO + 自动建立同级 CONNECTED。
        权限检查：caller 需要 create_agent 权限，parent_id 在 caller 子树内。
        """

    async def destroy_agent(self, caller_id: str, node_id: str) -> None:
        """
        销毁 Agent。删除节点、所有关系、inbox 中的消息。
        权限检查：caller 需要 destroy_agent 权限，node_id 在 caller 子树内。
        发布事件：node.destroyed
        """

    # === 人才池 ===

    async def talent_pool_query(self, caller_id: str, **filters) -> list[Node]:
        """查询内部人才池（status=SUSPENDED 的节点）。需要 recruit 权限。"""

    async def talent_pool_recruit(
        self,
        caller_id: str,
        node_id: str,           # 要招募的节点
        parent_id: str,         # 新上级
    ) -> Relation:
        """
        原子操作：激活节点（SUSPENDED→ONLINE）+ 建立 REPORTS_TO + 自动 CONNECTED。
        权限检查：caller 需要 recruit 权限。
        """

    async def talent_pool_release(self, caller_id: str, node_id: str) -> None:
        """
        原子操作：解除 REPORTS_TO + 解除所有 CONNECTED + 状态设为 SUSPENDED。
        权限检查：caller 是 node_id 的直接上级，或有 recruit 权限。
        """

    # === 审计 ===

    async def audit_inspect(self, caller_id: str, target_id: str) -> dict:
        """
        查看目标节点的状态和记忆。
        权限检查：caller 是 target 的直接上级（隐含），或有 audit 权限且 target 在子树内。
        返回：node 信息 + 记忆文件内容（由 runtime 提供）
        """

    # === 关系 ===

    async def relation_create(
        self,
        caller_id: str,
        node_a_id: str,
        node_b_id: str,
    ) -> Relation:
        """
        建立 CONNECTED 关系。
        权限检查：caller 是 node_a 或 node_b 的上级。
        发布事件：relation.created
        """

    async def relation_remove(self, caller_id: str, relation_id: str) -> None:
        """
        解除 CONNECTED 关系。
        权限检查：caller 是关系双方之一的上级。
        发布事件：relation.removed
        """

    # === 任务 ===

    async def task_create(self, caller_id: str, task: Task) -> Task:
        """创建任务。任何节点可为自己创建。发布事件：task.status_changed"""

    async def task_update(self, caller_id: str, task_id: str, **fields) -> Task:
        """更新任务。只有 creator 或 assignee 可更新。发布事件：task.status_changed"""

    async def task_query(self, caller_id: str, **filters) -> list[Task]:
        """查询任务。只返回 caller 有权限查看的任务（自己的 + 子树内的）。"""

    # === 消息 ===

    async def send_message(self, caller_id: str, message: Message) -> list[str]:
        """发送消息。委托给 MessageBus。"""

    # === 权限 ===

    async def grant_permission(self, caller_id: str, target_id: str, permission: str) -> None:
        """
        授予权限。
        权限检查：caller 需要 grant_permission 权限，且自己拥有要授予的权限。
        target_id 必须在 caller 子树内。
        发布事件：permission.granted
        """

    # === 事件 ===

    async def publish_event(self, caller_id: str, event: Event) -> None:
        """Agent 发布自定义事件。source 由系统填充为 caller_id。"""
```

## 权限检查辅助

```python
async def _check_permission(self, caller_id: str, permission: str) -> None:
    """检查 caller 是否有指定权限。无权限则抛 PermissionDeniedError。"""
    node = await self.store.get_node(caller_id)
    if node.is_owner:
        return  # Tyrant 拥有全部权限
    if not getattr(node.permissions, permission, False):
        raise PermissionDeniedError(f"{caller_id} lacks {permission}")

async def _check_in_subtree(self, caller_id: str, target_id: str) -> None:
    """检查 target 是否在 caller 的子树中。"""
    subtree = await self.store.get_subtree_node_ids(caller_id)
    if target_id not in subtree:
        raise PermissionDeniedError(f"{target_id} not in subtree of {caller_id}")

async def _is_direct_superior(self, caller_id: str, target_id: str) -> bool:
    """检查 caller 是否是 target 的直接上级。"""
    rel = await self.store.get_reports_to(target_id)
    return rel is not None and rel.node_a == caller_id
```

## create_agent 原子流程

```python
async def create_agent(self, caller_id, name, parent_id, agent_config, metadata):
    await self._check_permission(caller_id, "create_agent")
    await self._check_in_subtree(caller_id, parent_id)

    async with self.store.transaction():
        # 1. 创建节点
        node = Node(id=uuid4(), type=LOCAL_AGENT, name=name, ...)
        await self.store.save_node(node)

        # 2. 建立 REPORTS_TO
        rel = Relation(type=REPORTS_TO, node_a=parent_id, node_b=node.id, ...)
        await self.store.save_relation(rel)

        # 3. 自动建立同级 CONNECTED
        siblings = await self.store.get_subordinates(parent_id)
        for sib in siblings:
            if sib.node_b != node.id:
                conn = Relation(type=CONNECTED, node_a=node.id, node_b=sib.node_b, ...)
                await self.store.save_relation(conn)

    # 4. 发布事件
    await self.event_bus.publish(Event(type="relation.created", ...))
    return node
```
