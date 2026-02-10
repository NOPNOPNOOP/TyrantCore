"""Agent 运行时。ADK 集成 + tools + inbox 桥接。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

import structlog

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from tyrant_core.models import (
    Event,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Priority,
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

    def _create_adk_agent(self, node: Node) -> LlmAgent:
        config = node.metadata.get("agent_config", {})
        # 模型优先级：agent_config > 全局 Settings > 默认
        model = config.get("model") or self.settings.get_litellm_model()
        return LlmAgent(
            model=model,
            name=node.name,
            description=config.get("description", f"Agent {node.name}"),
            instruction=config.get(
                "instruction", "You are a helpful agent."
            ),
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
        ) -> dict:
            """更新任务状态或内容。"""
            fields: dict = {}
            if status:
                fields["status"] = TaskStatus(status)
            if content:
                fields["content"] = content
            result = await self.api.task_update(agent_id, task_id, **fields)
            return {"task_id": result.id, "status": result.status.value}

        async def query_tasks(
            assignee: str | None = None,
            status: str | None = None,
        ) -> dict:
            """查询任务。"""
            filters: dict = {}
            if assignee:
                filters["assignee"] = assignee
            if status:
                filters["status"] = TaskStatus(status)
            tasks = await self.api.task_query(agent_id, **filters)
            return {"tasks": [t.model_dump() for t in tasks]}

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

        return [
            send_message,
            create_task,
            update_task,
            query_tasks,
            create_agent,
            release_agent,
            query_talent_pool,
            recruit_agent,
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

                    response_text = ""
                    async for event in runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=user_content,
                    ):
                        if (
                            event.is_final_response()
                            and event.content
                            and event.content.parts
                        ):
                            response_text = event.content.parts[0].text

                    # 如果有回复，自动发回给发送者
                    if response_text:
                        reply = Message(
                            id=str(uuid4()),
                            timestamp=datetime.now(timezone.utc),
                            from_node=node.id,
                            to=message.from_node,
                            type="CHAT",
                            content={"text": response_text},
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

    # ── 消息格式转换 ───────────────────────────────────────────────────────

    @staticmethod
    def _format_message_for_adk(message: Message) -> types.Content:
        """将 TyrantCore Message 转为 ADK Content 对象。"""
        parts = [f"[From: {message.from_node}] [Type: {message.type}]"]
        if message.content:
            parts.append(json.dumps(message.content, ensure_ascii=False))
        text = "\n".join(parts)
        return types.Content(role="user", parts=[types.Part(text=text)])
