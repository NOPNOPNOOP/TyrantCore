"""系统 API。所有对外操作的唯一入口，权限检查在此层完成。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import structlog

from tyrant_core.models import (
    Endpoint,
    Event,
    Message,
    Node,
    NodeNotFoundError,
    NodeStatus,
    NodeType,
    Permissions,
    PermissionDeniedError,
    Relation,
    RelationType,
    Task,
)
from tyrant_core.store.base import Store
from tyrant_core.bus.event_bus import EventBus
from tyrant_core.bus.message_bus import MessageBus

logger = structlog.get_logger()


class SystemAPI:
    """所有对外操作的统一入口。"""

    def __init__(
        self,
        store: Store,
        message_bus: MessageBus,
        event_bus: EventBus,
    ) -> None:
        self.store = store
        self.message_bus = message_bus
        self.event_bus = event_bus

    # ═══ 权限检查辅助 ═══════════════════════════════════════════════════════

    async def _get_node_or_raise(self, node_id: str) -> Node:
        async with self.store.transaction():
            node = await self.store.get_node(node_id)
        if node is None:
            raise NodeNotFoundError(f"Node {node_id} not found")
        return node

    async def _check_permission(self, caller_id: str, permission: str) -> None:
        """检查 caller 是否有指定权限。Tyrant (is_owner) 拥有全部权限。"""
        node = await self._get_node_or_raise(caller_id)
        if node.is_owner:
            return
        if not getattr(node.permissions, permission, False):
            raise PermissionDeniedError(f"{caller_id} lacks {permission}")

    async def _check_in_subtree(self, caller_id: str, target_id: str) -> None:
        """检查 target 是否在 caller 的子树中。"""
        async with self.store.transaction():
            subtree = await self.store.get_subtree_node_ids(caller_id)
        if target_id not in subtree:
            raise PermissionDeniedError(
                f"{target_id} not in subtree of {caller_id}"
            )

    async def _is_direct_superior(self, caller_id: str, target_id: str) -> bool:
        """检查 caller 是否是 target 的直接上级。"""
        async with self.store.transaction():
            rel = await self.store.get_reports_to(target_id)
        return rel is not None and rel.node_a == caller_id

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ═══ Agent 生命周期 ═══════════════════════════════════════════════════════

    async def create_agent(
        self,
        caller_id: str,
        name: str,
        parent_id: str,
        agent_config: dict | None = None,
        metadata: dict | None = None,
    ) -> Node:
        """
        原子操作：创建节点 + 建立 REPORTS_TO + 自动建立同级 CONNECTED。

        权限检查：caller 需要 create_agent 权限，parent_id 在 caller 子树内。
        """
        await self._check_permission(caller_id, "create_agent")
        await self._check_in_subtree(caller_id, parent_id)

        now = self._now()
        node_id = str(uuid4())

        # agent_config 同时存入 node.metadata 供 runtime 读取
        node_metadata = metadata or {}
        if agent_config:
            node_metadata["agent_config"] = agent_config

        node = Node(
            id=node_id,
            type=NodeType.LOCAL_AGENT,
            name=name,
            endpoint=Endpoint(protocol="local", address=node_id),
            status=NodeStatus.ONLINE,
            metadata=node_metadata,
            created_at=now,
        )

        async with self.store.transaction():
            # 1. 创建节点
            await self.store.save_node(node)

            # 2. 建立 REPORTS_TO
            reports_rel = Relation(
                id=str(uuid4()),
                type=RelationType.REPORTS_TO,
                node_a=parent_id,
                node_b=node_id,
                config=agent_config or {},
                created_at=now,
            )
            await self.store.save_relation(reports_rel)

            # 3. 自动建立同级 CONNECTED
            siblings = await self.store.get_subordinates(parent_id)
            for sib in siblings:
                if sib.node_b != node_id:
                    conn = Relation(
                        id=str(uuid4()),
                        type=RelationType.CONNECTED,
                        node_a=node_id,
                        node_b=sib.node_b,
                        created_at=now,
                    )
                    await self.store.save_relation(conn)

        # 4. 发布事件
        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="node.created",
                timestamp=now,
                source="system",
                data={"node_id": node_id, "name": name, "parent_id": parent_id},
            ))

        logger.info("agent_created", node_id=node_id, name=name, parent_id=parent_id)
        return node

    async def destroy_agent(self, caller_id: str, node_id: str) -> None:
        """
        销毁 Agent。删除节点、所有关系。

        权限检查：caller 需要 destroy_agent 权限，node_id 在 caller 子树内。
        """
        await self._check_permission(caller_id, "destroy_agent")
        await self._check_in_subtree(caller_id, node_id)

        async with self.store.transaction():
            # 删除所有关系
            relations = await self.store.get_relations_for_node(node_id)
            for rel in relations:
                await self.store.delete_relation(rel.id)
            # 删除节点
            await self.store.delete_node(node_id)

        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="node.destroyed",
                timestamp=self._now(),
                source="system",
                data={"node_id": node_id},
            ))

        logger.info("agent_destroyed", node_id=node_id)

    # ═══ 人才池 ═══════════════════════════════════════════════════════════════

    async def talent_pool_query(
        self, caller_id: str, **filters
    ) -> list[Node]:
        """查询内部人才池（status=SUSPENDED 的节点）。需要 recruit 权限。"""
        await self._check_permission(caller_id, "recruit")
        async with self.store.transaction():
            return await self.store.list_nodes(status=NodeStatus.SUSPENDED, **filters)

    async def talent_pool_recruit(
        self,
        caller_id: str,
        node_id: str,
        parent_id: str,
    ) -> Relation:
        """
        原子操作：激活节点 + 建立 REPORTS_TO + 自动 CONNECTED。

        权限检查：caller 需要 recruit 权限。
        """
        await self._check_permission(caller_id, "recruit")

        now = self._now()

        async with self.store.transaction():
            # 激活节点
            await self.store.update_node(node_id, status=NodeStatus.ONLINE)

            # 建立 REPORTS_TO
            rel = Relation(
                id=str(uuid4()),
                type=RelationType.REPORTS_TO,
                node_a=parent_id,
                node_b=node_id,
                created_at=now,
            )
            await self.store.save_relation(rel)

            # 自动建立同级 CONNECTED
            siblings = await self.store.get_subordinates(parent_id)
            for sib in siblings:
                if sib.node_b != node_id:
                    conn = Relation(
                        id=str(uuid4()),
                        type=RelationType.CONNECTED,
                        node_a=node_id,
                        node_b=sib.node_b,
                        created_at=now,
                    )
                    await self.store.save_relation(conn)

        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="node.recruited",
                timestamp=now,
                source="system",
                data={"node_id": node_id, "parent_id": parent_id},
            ))

        logger.info("agent_recruited", node_id=node_id, parent_id=parent_id)
        return rel

    async def talent_pool_release(self, caller_id: str, node_id: str) -> None:
        """
        原子操作：解除 REPORTS_TO + 解除所有 CONNECTED + 状态设为 SUSPENDED。

        权限检查：caller 是 node_id 的直接上级，或有 recruit 权限。
        """
        is_superior = await self._is_direct_superior(caller_id, node_id)
        if not is_superior:
            await self._check_permission(caller_id, "recruit")

        async with self.store.transaction():
            # 删除所有关系
            relations = await self.store.get_relations_for_node(node_id)
            for rel in relations:
                await self.store.delete_relation(rel.id)
            # 设为 SUSPENDED
            await self.store.update_node(node_id, status=NodeStatus.SUSPENDED)

        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="node.released",
                timestamp=self._now(),
                source="system",
                data={"node_id": node_id},
            ))

        logger.info("agent_released", node_id=node_id)

    # ═══ 审计 ═══════════════════════════════════════════════════════════════

    async def audit_inspect(self, caller_id: str, target_id: str) -> dict:
        """
        查看目标节点的状态和记忆。

        权限检查：caller 是 target 的直接上级，或有 audit 权限且 target 在子树内。
        """
        is_superior = await self._is_direct_superior(caller_id, target_id)
        if not is_superior:
            await self._check_permission(caller_id, "audit")
            await self._check_in_subtree(caller_id, target_id)

        async with self.store.transaction():
            node = await self.store.get_node(target_id)
            if node is None:
                raise NodeNotFoundError(f"Node {target_id} not found")
            relations = await self.store.get_relations_for_node(target_id)
            tasks = await self.store.query_tasks(assignee=target_id)

        return {
            "node": node.model_dump(),
            "relations": [r.model_dump() for r in relations],
            "tasks": [t.model_dump() for t in tasks],
        }

    # ═══ 关系 ═══════════════════════════════════════════════════════════════

    async def relation_create(
        self,
        caller_id: str,
        node_a_id: str,
        node_b_id: str,
    ) -> Relation:
        """
        建立 CONNECTED 关系。

        权限检查：caller 是 node_a 或 node_b 的上级。
        """
        is_sup_a = await self._is_direct_superior(caller_id, node_a_id)
        is_sup_b = await self._is_direct_superior(caller_id, node_b_id)
        # 也允许 caller 本身是其中之一
        caller_node = await self._get_node_or_raise(caller_id)
        if not (is_sup_a or is_sup_b or caller_node.is_owner):
            raise PermissionDeniedError(
                f"{caller_id} is not superior of {node_a_id} or {node_b_id}"
            )

        now = self._now()
        rel = Relation(
            id=str(uuid4()),
            type=RelationType.CONNECTED,
            node_a=node_a_id,
            node_b=node_b_id,
            created_at=now,
        )

        async with self.store.transaction():
            await self.store.save_relation(rel)

        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="relation.created",
                timestamp=now,
                source="system",
                data={
                    "relation_id": rel.id,
                    "node_a": node_a_id,
                    "node_b": node_b_id,
                },
            ))

        return rel

    async def relation_remove(self, caller_id: str, relation_id: str) -> None:
        """
        解除 CONNECTED 关系。

        权限检查：caller 是关系双方之一的上级。
        """
        async with self.store.transaction():
            rel = await self.store.get_relation(relation_id)
        if rel is None:
            from tyrant_core.models import RelationNotFoundError
            raise RelationNotFoundError(f"Relation {relation_id} not found")

        is_sup_a = await self._is_direct_superior(caller_id, rel.node_a)
        is_sup_b = await self._is_direct_superior(caller_id, rel.node_b)
        caller_node = await self._get_node_or_raise(caller_id)
        if not (is_sup_a or is_sup_b or caller_node.is_owner):
            raise PermissionDeniedError(
                f"{caller_id} cannot remove relation {relation_id}"
            )

        async with self.store.transaction():
            await self.store.delete_relation(relation_id)

        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="relation.removed",
                timestamp=self._now(),
                source="system",
                data={"relation_id": relation_id},
            ))

    # ═══ 任务 ═══════════════════════════════════════════════════════════════

    async def task_create(self, caller_id: str, task: Task) -> Task:
        """创建任务。任何节点可为自己创建。"""
        async with self.store.transaction():
            await self.store.save_task(task)

        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="task.status_changed",
                timestamp=self._now(),
                source=caller_id,
                data={"task_id": task.id, "status": task.status.value},
            ))

        return task

    async def task_update(
        self, caller_id: str, task_id: str, **fields
    ) -> Task:
        """更新任务。只有 creator 或 assignee 可更新。"""
        async with self.store.transaction():
            task = await self.store.get_task(task_id)
        if task is None:
            from tyrant_core.models import TaskNotFoundError
            raise TaskNotFoundError(f"Task {task_id} not found")

        if caller_id not in (task.creator, task.assignee):
            # 检查是否是 owner
            caller_node = await self._get_node_or_raise(caller_id)
            if not caller_node.is_owner:
                raise PermissionDeniedError(
                    f"{caller_id} cannot update task {task_id}"
                )

        fields["updated_at"] = self._now()

        async with self.store.transaction():
            await self.store.update_task(task_id, **fields)
            updated = await self.store.get_task(task_id)

        if "status" in fields:
            async with self.store.transaction():
                await self.event_bus.publish(Event(
                    id=str(uuid4()),
                    type="task.status_changed",
                    timestamp=self._now(),
                    source=caller_id,
                    data={
                        "task_id": task_id,
                        "status": fields["status"].value
                        if hasattr(fields["status"], "value")
                        else fields["status"],
                    },
                ))

        return updated  # type: ignore[return-value]

    async def task_query(self, caller_id: str, **filters) -> list[Task]:
        """查询任务。只返回 caller 有权限查看的任务。"""
        async with self.store.transaction():
            return await self.store.query_tasks(**filters)

    # ═══ 消息 ═══════════════════════════════════════════════════════════════

    async def send_message(
        self, caller_id: str, message: Message
    ) -> list[str]:
        """发送消息。委托给 MessageBus。"""
        async with self.store.transaction():
            return await self.message_bus.send(message, sender_id=caller_id)

    # ═══ 权限 ═══════════════════════════════════════════════════════════════

    async def grant_permission(
        self,
        caller_id: str,
        target_id: str,
        permission: str,
    ) -> None:
        """
        授予权限。

        权限检查：caller 需要 grant_permission 权限，且自己拥有要授予的权限。
        target_id 必须在 caller 子树内。
        """
        await self._check_permission(caller_id, "grant_permission")
        await self._check_permission(caller_id, permission)
        await self._check_in_subtree(caller_id, target_id)

        async with self.store.transaction():
            target = await self.store.get_node(target_id)
            if target is None:
                raise NodeNotFoundError(f"Node {target_id} not found")
            perms = target.permissions.model_dump()
            perms[permission] = True
            await self.store.update_node(
                target_id, permissions=Permissions(**perms)
            )

        async with self.store.transaction():
            await self.event_bus.publish(Event(
                id=str(uuid4()),
                type="permission.granted",
                timestamp=self._now(),
                source=caller_id,
                data={
                    "target_id": target_id,
                    "permission": permission,
                },
            ))

        logger.info(
            "permission_granted",
            caller_id=caller_id,
            target_id=target_id,
            permission=permission,
        )

    # ═══ 事件 ═══════════════════════════════════════════════════════════════

    async def publish_event(self, caller_id: str, event: Event) -> None:
        """Agent 发布自定义事件。source 由系统填充为 caller_id。"""
        event = event.model_copy(update={"source": caller_id})
        async with self.store.transaction():
            await self.event_bus.publish(event)
