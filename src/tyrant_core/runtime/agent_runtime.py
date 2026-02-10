"""Agent 运行时。ADK 集成 + tools + inbox 桥接。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

import structlog

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from tyrant_core.models import (
    ConversationTurn,
    Event,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Priority,
    RefType,
    Reference,
    Scope,
    ScopeType,
    Task,
    TaskStatus,
)
from tyrant_core.api.system_api import SystemAPI
from tyrant_core.bus.event_bus import EventBus
from tyrant_core.bus.message_bus import MessageBus
from tyrant_core.config import Settings, get_settings

logger = structlog.get_logger()

APP_NAME = "tyrant_core"


class AgentRuntime:
    """管理 LOCAL_AGENT 的生命周期。"""

    def __init__(
        self,
        api: SystemAPI,
        message_bus: MessageBus,
        event_bus: EventBus,
        settings: Settings | None = None,
    ) -> None:
        self.api = api
        self.message_bus = message_bus
        self.event_bus = event_bus
        self.settings = settings or get_settings()
        self._session_service = InMemorySessionService()
        self._agents: dict[str, asyncio.Task] = {}  # node_id → agent_loop task
        self._runners: dict[str, Runner] = {}  # node_id → Runner

    # ── 生命周期 ───────────────────────────────────────────────────────────

    async def start_agent(self, node: Node) -> None:
        """启动一个 Agent（创建 ADK Agent 实例 + 启动 inbox 监听协程）。"""
        if node.id in self._agents:
            logger.warning("agent_already_running", node_id=node.id)
            return

        adk_agent = self._create_adk_agent(node)
        runner = self._create_runner(adk_agent)
        self._runners[node.id] = runner

        task = asyncio.create_task(
            self._agent_loop(node, runner),
            name=f"agent-{node.id}",
        )
        self._agents[node.id] = task
        logger.info("agent_started", node_id=node.id, name=node.name)

    async def stop_agent(self, node_id: str) -> None:
        """停止一个 Agent（取消协程 + 清理内存状态）。"""
        task = self._agents.pop(node_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._runners.pop(node_id, None)
        logger.info("agent_stopped", node_id=node_id)

    async def recover(self) -> None:
        """系统重启：从 store 恢复所有 ONLINE 的 LOCAL_AGENT 并启动。"""
        async with self.api.store.transaction():
            nodes = await self.api.store.list_nodes(
                type=NodeType.LOCAL_AGENT, status=NodeStatus.ONLINE
            )
        for node in nodes:
            await self.start_agent(node)
        logger.info("runtime_recovered", count=len(nodes))

    async def shutdown(self) -> None:
        """优雅关闭所有 Agent。"""
        node_ids = list(self._agents.keys())
        for node_id in node_ids:
            await self.stop_agent(node_id)
        logger.info("runtime_shutdown")

    # ── ADK Agent 创建 ─────────────────────────────────────────────────────

    # ── 默认 system prompt ────────────────────────────────────────────────

    DEFAULT_INSTRUCTION = """你是 TyrantCore 网络中的一个 Agent 节点。

## 回复机制
- 你的文本回复会**自动发送**给当前消息的发送者，无需手动调用 send_message。
- send_message 用于**主动联系其他节点**（如下发任务、向上级汇报），不用于回复当前消息。
- 直接用自然语言回复即可，系统会自动处理发送。

## 核心行为
当你收到任务或请求时：
1. 评估你是否能直接完成。如果能，立即执行并汇报结果。
2. 如果任务复杂需要拆分：
   - 用 create_agent 创建专门的下属，或用 recruit_agent 从人才池招募
   - 用 create_task 为每个子任务建立追踪
   - 用 send_message 将子任务下发给对应的下属
3. 等待下属完成（你会收到 NOTIFICATION 类型的任务完成通知）
4. 用 query_tasks 随时查看你创建的任务状态（不要靠记忆）
5. 所有子任务完成后，汇总结果，向上级汇报

## 消息处理
- 收到 NOTIFICATION（event=task.completed/task.failed）：检查是否所有子任务都完成
- 收到普通消息（下属提问等）：正常回复
- 汇报结果时，用 update_task 将你的任务标记为 COMPLETED 并附带 result

## 原则
- 你的行为决策由你自己做出，基础设施只提供能力
- 尽量并行分配子任务，减少等待时间
- 如果遇到问题，向上级（消息发送者）提问而不是猜测
"""

    def _create_adk_agent(self, node: Node) -> LlmAgent:
        config = node.metadata.get("agent_config", {})
        # 模型优先级：agent_config > 全局 Settings > 默认
        model = config.get("model") or self.settings.get_litellm_model()
        return LlmAgent(
            model=model,
            name=node.name,
            description=config.get("description", f"Agent {node.name}"),
            instruction=config.get("instruction", self.DEFAULT_INSTRUCTION),
            tools=self._build_tools(node.id),
        )

    def _create_runner(self, agent: LlmAgent) -> Runner:
        return Runner(
            agent=agent,
            app_name=APP_NAME,
            session_service=self._session_service,
        )

    # ── Tool 暴露 ──────────────────────────────────────────────────────────

    def _build_tools(self, agent_id: str) -> list[Callable]:
        """为指定 Agent 构建可用的 tool 函数列表。"""

        async def send_message(
            to: str, type: str = "CHAT", content: dict | None = None
        ) -> dict:
            """向指定节点发送消息。"""
            msg = Message(
                id=str(uuid4()),
                timestamp=datetime.now(timezone.utc),
                from_node=agent_id,
                to=to,
                type=type,
                content=content or {},
            )
            ids = await self.api.send_message(agent_id, msg)
            return {"sent": len(ids), "message_ids": ids}

        async def create_task(
            assignee: str,
            content: dict | None = None,
            parent_task: str | None = None,
        ) -> dict:
            """创建新任务。"""
            now = datetime.now(timezone.utc)
            task = Task(
                id=str(uuid4()),
                creator=agent_id,
                assignee=assignee,
                content=content or {},
                parent_task=parent_task,
                created_at=now,
                updated_at=now,
            )
            result = await self.api.task_create(agent_id, task)
            return {"task_id": result.id}

        async def update_task(
            task_id: str,
            status: str | None = None,
            content: dict | None = None,
            result: dict | None = None,
        ) -> dict:
            """更新任务状态或内容。完成时可附带 result。"""
            fields: dict = {}
            if status:
                fields["status"] = TaskStatus(status)
            if content:
                fields["content"] = content
            if result is not None:
                fields["result"] = result
            updated = await self.api.task_update(agent_id, task_id, **fields)
            return {"task_id": updated.id, "status": updated.status.value}

        async def query_tasks(
            assignee: str | None = None,
            creator: str | None = None,
            status: str | None = None,
        ) -> dict:
            """查询任务。可按 assignee、creator、status 过滤。"""
            filters: dict = {}
            if assignee:
                filters["assignee"] = assignee
            if creator:
                filters["creator"] = creator
            if status:
                filters["status"] = TaskStatus(status)
            tasks = await self.api.task_query(agent_id, **filters)
            return {
                "tasks": [
                    {
                        "id": t.id,
                        "status": t.status.value,
                        "assignee": t.assignee,
                        "creator": t.creator,
                        "content": t.content,
                        "result": t.result,
                        "parent_task": t.parent_task,
                    }
                    for t in tasks
                ]
            }

        async def create_agent(
            name: str,
            instruction: str,
        ) -> dict:
            """创建新的下属 Agent。"""
            node = await self.api.create_agent(
                caller_id=agent_id,
                name=name,
                parent_id=agent_id,
                agent_config={"instruction": instruction},
            )
            await self.start_agent(node)
            return {"agent_id": node.id, "name": node.name}

        async def release_agent(agent_id_to_release: str) -> dict:
            """释放下属 Agent 到人才池。"""
            await self.api.talent_pool_release(agent_id, agent_id_to_release)
            await self.stop_agent(agent_id_to_release)
            return {"released": agent_id_to_release}

        async def query_talent_pool() -> dict:
            """查询人才池。"""
            nodes = await self.api.talent_pool_query(agent_id)
            return {
                "agents": [
                    {"id": n.id, "name": n.name, "metadata": n.metadata}
                    for n in nodes
                ]
            }

        async def recruit_agent(target_id: str) -> dict:
            """从人才池招募 Agent。"""
            await self.api.talent_pool_recruit(
                agent_id, target_id, parent_id=agent_id
            )
            async with self.api.store.transaction():
                node = await self.api.store.get_node(target_id)
            if node:
                await self.start_agent(node)
            return {"recruited": target_id}

        async def create_delegation(
            delegate_id: str,
            action: str,
            scope_type: str = "self_subtree",
            scope_targets: list[str] | None = None,
            one_time: bool = False,
        ) -> dict:
            """将自己的权限委托给另一个节点。scope_type: global/self_subtree/subtrees/nodes。不可再委托。"""
            scope = Scope(
                type=ScopeType(scope_type),
                targets=scope_targets or [],
            )
            d = await self.api.create_delegation(
                caller_id=agent_id,
                delegate_id=delegate_id,
                action=action,
                scope=scope,
                one_time=one_time,
            )
            return {"delegation_id": d.id}

        async def revoke_delegation(delegation_id: str) -> dict:
            """撤销一个委托。"""
            await self.api.revoke_delegation(agent_id, delegation_id)
            return {"revoked": delegation_id}

        async def query_messages(
            node_id: str | None = None,
            from_node: str | None = None,
            to_node: str | None = None,
            type: str | None = None,
            limit: int = 50,
        ) -> dict:
            """查询消息。默认查自己参与的消息。可按 from_node/to_node/type 过滤。"""
            target = node_id or agent_id
            msgs = await self.api.query_messages(
                caller_id=agent_id,
                node_id=target,
                from_node=from_node,
                to_node=to_node,
                type=type,
                limit=limit,
            )
            return {
                "messages": [
                    {
                        "id": m.id,
                        "from": m.from_node,
                        "to": m.to,
                        "type": m.type,
                        "content": m.content,
                        "timestamp": str(m.timestamp),
                    }
                    for m in msgs
                ]
            }

        async def query_conversation_history(
            target_id: str,
            trigger_message_id: str | None = None,
            limit: int = 50,
        ) -> dict:
            """查询目标节点的 LLM 对话历史。查自己或下属（需 audit 权限）。"""
            turns = await self.api.query_conversation_history(
                caller_id=agent_id,
                target_id=target_id,
                trigger_message_id=trigger_message_id,
                limit=limit,
            )
            return {
                "turns": [
                    {
                        "id": t.id,
                        "role": t.role,
                        "content": t.content,
                        "tool_calls": t.tool_calls,
                        "tool_result": t.tool_result,
                        "trigger_message_id": t.trigger_message_id,
                        "timestamp": str(t.timestamp),
                    }
                    for t in turns
                ]
            }

        return [
            send_message,
            create_task,
            update_task,
            query_tasks,
            create_agent,
            release_agent,
            query_talent_pool,
            recruit_agent,
            create_delegation,
            revoke_delegation,
            query_messages,
            query_conversation_history,
        ]

    # ── Inbox → ADK 桥接 ──────────────────────────────────────────────────

    async def _agent_loop(self, node: Node, runner: Runner) -> None:
        """持续监听 inbox，通过 Runner.run_async() 处理消息。"""
        user_id = node.id
        session_id = f"session_{node.id}"

        # 创建 ADK session（招募回来的 agent 可能已有 session）
        try:
            await self._session_service.create_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception:
            # session 已存在，继续使用
            pass

        try:
            async for message in self.message_bus.subscribe_inbox(node.id):
                try:
                    user_content = self._format_message_for_adk(message)

                    # 写入 user turn
                    await self._save_turn(
                        node_id=node.id,
                        role="user",
                        content=user_content.parts[0].text,
                        trigger_message_id=message.id,
                    )

                    response_text = ""
                    async for event in runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=user_content,
                    ):
                        is_final = event.is_final_response()
                        # 提取并写入 model/tool turns（final response 跳过）
                        await self._extract_and_save_turns(
                            node_id=node.id,
                            event=event,
                            trigger_message_id=message.id,
                            is_final=is_final,
                        )
                        if (
                            is_final
                            and event.content
                            and event.content.parts
                        ):
                            response_text = event.content.parts[0].text

                    # 如果有回复，自动发回给发送者（带 reply_to reference）
                    if response_text:
                        reply = Message(
                            id=str(uuid4()),
                            timestamp=datetime.now(timezone.utc),
                            from_node=node.id,
                            to=message.from_node,
                            type="CHAT",
                            content={"text": response_text},
                            references=[Reference(
                                ref_type=RefType.MESSAGE,
                                ref_id=message.id,
                                relation="reply_to",
                            )],
                        )
                        try:
                            await self.api.send_message(node.id, reply)
                        except Exception:
                            logger.warning(
                                "reply_failed",
                                node_id=node.id,
                                to=message.from_node,
                            )

                    # ACK
                    if message.requires_ack:
                        await self.message_bus.ack(message.id, node.id)

                except Exception:
                    logger.exception(
                        "agent_message_processing_failed",
                        node_id=node.id,
                        message_id=message.id,
                    )
                    async with self.api.store.transaction():
                        await self.event_bus.publish(Event(
                            id=str(uuid4()),
                            type="agent.error",
                            timestamp=datetime.now(timezone.utc),
                            source=node.id,
                            data={"message_id": message.id},
                        ))
        except asyncio.CancelledError:
            logger.info("agent_loop_cancelled", node_id=node.id)
            raise

    # ── ConversationTurn 写入（唯一框架耦合点）─────────────────────────────

    async def _save_turn(
        self,
        node_id: str,
        role: str,
        content: str = "",
        tool_calls: list[dict] | None = None,
        tool_result: dict | None = None,
        trigger_message_id: str | None = None,
    ) -> None:
        """写入一条 ConversationTurn 到 store。"""
        turn = ConversationTurn(
            id=str(uuid4()),
            node_id=node_id,
            role=role,
            content=content,
            tool_calls=tool_calls or [],
            tool_result=tool_result or {},
            trigger_message_id=trigger_message_id,
            timestamp=datetime.now(timezone.utc),
        )
        try:
            async with self.api.store.transaction():
                await self.api.store.save_conversation_turn(turn)
        except Exception:
            logger.warning("save_turn_failed", node_id=node_id, role=role)

    async def _extract_and_save_turns(
        self,
        node_id: str,
        event: Any,
        trigger_message_id: str | None = None,
        is_final: bool = False,
    ) -> None:
        """
        从 ADK event 提取 model/tool_call/tool_result turns 并写入 store。
        这是唯一碰框架的地方。换框架只改这段逻辑。

        is_final=True 时跳过 model 文本（final response 只存在于 reply Message 中）。
        中间 model 文本（tool call 之间的思考）仍写入。
        """
        if not event.content or not event.content.parts:
            return

        for part in event.content.parts:
            # model 文本回复（final response 跳过，只存为 reply Message）
            if hasattr(part, "text") and part.text and event.content.role == "model":
                if is_final:
                    continue
                await self._save_turn(
                    node_id=node_id,
                    role="model",
                    content=part.text,
                    trigger_message_id=trigger_message_id,
                )
            # tool call (function_call)
            elif hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                await self._save_turn(
                    node_id=node_id,
                    role="tool_call",
                    tool_calls=[{
                        "name": fc.name,
                        "args": dict(fc.args) if fc.args else {},
                    }],
                    trigger_message_id=trigger_message_id,
                )
            # tool result (function_response)
            elif hasattr(part, "function_response") and part.function_response:
                fr = part.function_response
                await self._save_turn(
                    node_id=node_id,
                    role="tool_result",
                    tool_result={
                        "name": fr.name,
                        "result": dict(fr.response) if fr.response else {},
                    },
                    trigger_message_id=trigger_message_id,
                )

    # ── 消息格式转换 ───────────────────────────────────────────────────────

    @staticmethod
    def _format_message_for_adk(message: Message) -> types.Content:
        """将 TyrantCore Message 转为 ADK Content 对象。"""
        parts = [f"[From: {message.from_node}] [Type: {message.type}]"]
        if message.content:
            parts.append(json.dumps(message.content, ensure_ascii=False))
        text = "\n".join(parts)
        return types.Content(role="user", parts=[types.Part(text=text)])
