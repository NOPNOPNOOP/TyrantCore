"""端到端测试：消息查询 + 对话历史可观测性。

模拟 ADK Runner，验证全链路：
  发消息 → agent_loop 处理 → turns 写入 → 消息/对话历史查询 + 权限
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.models import (
    ConversationTurn,
    Endpoint,
    Message,
    Node,
    NodeStatus,
    NodeType,
    Permission,
    PermissionDeniedError,
    PermissionEffect,
    RefType,
    Scope,
    ScopeType,
)
from tyrant_core.store.sqlite_store import SqliteStore
from tyrant_core.bus.event_bus import EventBus
from tyrant_core.bus.message_bus import MessageBus
from tyrant_core.api.system_api import SystemAPI
from tyrant_core.runtime.agent_runtime import AgentRuntime

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


# ── Mock ADK 对象 ──────────────────────────────────────────────────────────


@dataclass
class FakePart:
    """模拟 ADK Part。"""
    text: str | None = None
    function_call: object | None = None
    function_response: object | None = None


@dataclass
class FakeContent:
    """模拟 ADK Content。"""
    role: str = "model"
    parts: list[FakePart] = field(default_factory=list)


@dataclass
class FakeFunctionCall:
    name: str = ""
    args: dict = field(default_factory=dict)


@dataclass
class FakeFunctionResponse:
    name: str = ""
    response: dict = field(default_factory=dict)


@dataclass
class FakeEvent:
    """模拟 ADK Event。"""
    content: FakeContent | None = None
    _is_final: bool = False

    def is_final_response(self) -> bool:
        return self._is_final


class FakeRunner:
    """
    模拟 ADK Runner。

    每次 run_async 按顺序返回预设的 events 序列。
    """

    def __init__(self, event_sequences: list[list[FakeEvent]] | None = None):
        self._sequences = list(event_sequences or [])
        self._call_count = 0

    async def run_async(self, **kwargs):
        if self._call_count < len(self._sequences):
            events = self._sequences[self._call_count]
        else:
            # 默认：一个简单的 model 文本回复
            events = [
                FakeEvent(
                    content=FakeContent(
                        role="model",
                        parts=[FakePart(text="default reply")],
                    ),
                    _is_final=True,
                ),
            ]
        self._call_count += 1
        for event in events:
            yield event


class FakeSessionService:
    """模拟 ADK SessionService。"""

    async def create_session(self, **kwargs):
        pass


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
async def stack():
    """组装完整的测试栈。"""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    store = SqliteStore(engine)
    await store.initialize()

    event_bus = EventBus(store)
    message_bus = MessageBus(store, event_bus)
    api = SystemAPI(store, message_bus, event_bus)
    runtime = AgentRuntime(api, message_bus, event_bus)
    # 替换 session service 为 fake
    runtime._session_service = FakeSessionService()

    yield {
        "store": store,
        "event_bus": event_bus,
        "message_bus": message_bus,
        "api": api,
        "runtime": runtime,
    }

    await runtime.shutdown()
    await store.close()


async def _create_tyrant(store: SqliteStore) -> Node:
    tyrant = Node(
        id="tyrant",
        type=NodeType.HUMAN,
        name="Tyrant",
        is_owner=True,
        endpoint=Endpoint(protocol="cli", address="local"),
        status=NodeStatus.ONLINE,
        permissions=[],
        created_at=NOW,
    )
    async with store.transaction():
        await store.save_node(tyrant)
    return tyrant


# ── E2E: 完整消息处理流程 ──────────────────────────────────────────────────


class TestE2EAgentLoop:
    """测试 agent_loop 全链路：发消息 → 处理 → turns 写入 → 回复。"""

    async def test_message_triggers_turns_and_reply(self, stack):
        """
        Tyrant 发消息给 Agent →
        Agent 处理（模拟 model 回复） →
        user turn 写入 store，final model turn 不写 →
        Agent 自动回复给 Tyrant（带 reply_to reference）
        """
        store, api, runtime, message_bus = (
            stack["store"], stack["api"], stack["runtime"], stack["message_bus"],
        )
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="Worker", parent_id="tyrant",
        )

        # 预设 Runner：返回一个 model 文本回复（is_final=True）
        fake_runner = FakeRunner([
            [
                FakeEvent(
                    content=FakeContent(
                        role="model",
                        parts=[FakePart(text="收到，我来处理")],
                    ),
                    _is_final=True,
                ),
            ],
        ])

        # 手动启动 agent_loop（用 fake runner）
        loop_task = asyncio.create_task(
            runtime._agent_loop(agent, fake_runner),
        )

        # Tyrant 发送消息
        msg = Message(
            id="msg-001",
            timestamp=datetime.now(timezone.utc),
            from_node="tyrant",
            to=agent.id,
            type="CHAT",
            content={"text": "帮我查一下数据"},
        )
        await api.send_message(caller_id="tyrant", message=msg)

        # 等待处理完成
        await asyncio.sleep(0.3)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        # 验证 turns: final response 不应写入，只有 user turn
        async with store.transaction():
            turns = await store.query_conversation_turns(agent.id)

        assert len(turns) == 1
        assert turns[0].role == "user"
        assert "帮我查一下数据" in turns[0].content
        assert turns[0].trigger_message_id == "msg-001"

        # 验证 Agent 自动回复到了 Tyrant 的 inbox
        async with store.transaction():
            tyrant_msgs = await store.query_messages(to_node="tyrant")
        replies = [m for m in tyrant_msgs if m.from_node == agent.id]
        assert len(replies) == 1
        assert replies[0].content["text"] == "收到，我来处理"

        # 验证 reply Message 包含 reply_to reference
        reply = replies[0]
        assert len(reply.references) == 1
        assert reply.references[0].ref_type == RefType.MESSAGE
        assert reply.references[0].ref_id == "msg-001"
        assert reply.references[0].relation == "reply_to"

    async def test_tool_call_and_result_turns(self, stack):
        """
        模拟 Agent 调用 tool + tool 返回结果 + 最终 model 回复。
        验证 tool_call 和 tool_result turns 正确写入。
        """
        store, api, runtime = stack["store"], stack["api"], stack["runtime"]
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="ToolUser", parent_id="tyrant",
        )

        # 预设 Runner：tool_call → tool_result → model 回复
        fake_runner = FakeRunner([
            [
                # 1. model 发起 tool call
                FakeEvent(
                    content=FakeContent(
                        role="model",
                        parts=[FakePart(
                            function_call=FakeFunctionCall(
                                name="query_tasks",
                                args={"assignee": agent.id},
                            ),
                        )],
                    ),
                ),
                # 2. tool 返回结果
                FakeEvent(
                    content=FakeContent(
                        role="tool",
                        parts=[FakePart(
                            function_response=FakeFunctionResponse(
                                name="query_tasks",
                                response={"tasks": []},
                            ),
                        )],
                    ),
                ),
                # 3. model 最终回复
                FakeEvent(
                    content=FakeContent(
                        role="model",
                        parts=[FakePart(text="没有待处理的任务")],
                    ),
                    _is_final=True,
                ),
            ],
        ])

        loop_task = asyncio.create_task(
            runtime._agent_loop(agent, fake_runner),
        )

        msg = Message(
            id="msg-002",
            timestamp=datetime.now(timezone.utc),
            from_node="tyrant",
            to=agent.id,
            type="CHAT",
            content={"text": "查看任务"},
        )
        await api.send_message(caller_id="tyrant", message=msg)

        await asyncio.sleep(0.3)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        # 验证 turns: final model text 不写入，只有 user + tool_call + tool_result
        async with store.transaction():
            turns = await store.query_conversation_turns(agent.id)

        roles = [t.role for t in turns]
        assert roles == ["user", "tool_call", "tool_result"]

        # tool_call turn
        tc = turns[1]
        assert tc.tool_calls == [{"name": "query_tasks", "args": {"assignee": agent.id}}]

        # tool_result turn
        tr = turns[2]
        assert tr.tool_result == {"name": "query_tasks", "result": {"tasks": []}}

        # final model text 不应出现在 turns 中
        model_turns = [t for t in turns if t.role == "model"]
        assert len(model_turns) == 0

        # 所有 turns 都关联同一个 trigger_message_id
        for t in turns:
            assert t.trigger_message_id == "msg-002"

        # 验证 reply Message 包含 reply_to reference
        async with store.transaction():
            tyrant_msgs = await store.query_messages(to_node="tyrant")
        replies = [m for m in tyrant_msgs if m.from_node == agent.id]
        assert len(replies) == 1
        assert replies[0].references[0].relation == "reply_to"
        assert replies[0].references[0].ref_id == "msg-002"

    async def test_intermediate_model_text_saved(self, stack):
        """
        中间 model 文本（tool call 之间的思考）仍应写入 turns，
        只有 final response 跳过。
        """
        store, api, runtime = stack["store"], stack["api"], stack["runtime"]
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="Thinker", parent_id="tyrant",
        )

        # 中间 model 文本（非 final） → tool_call → tool_result → final model
        fake_runner = FakeRunner([
            [
                # 1. 中间 model 思考文本（非 final）
                FakeEvent(
                    content=FakeContent(
                        role="model",
                        parts=[FakePart(text="让我先查一下任务")],
                    ),
                    _is_final=False,
                ),
                # 2. tool call
                FakeEvent(
                    content=FakeContent(
                        role="model",
                        parts=[FakePart(
                            function_call=FakeFunctionCall(
                                name="query_tasks", args={},
                            ),
                        )],
                    ),
                ),
                # 3. tool result
                FakeEvent(
                    content=FakeContent(
                        role="tool",
                        parts=[FakePart(
                            function_response=FakeFunctionResponse(
                                name="query_tasks",
                                response={"tasks": []},
                            ),
                        )],
                    ),
                ),
                # 4. final model 回复（不写 turn）
                FakeEvent(
                    content=FakeContent(
                        role="model",
                        parts=[FakePart(text="任务列表为空")],
                    ),
                    _is_final=True,
                ),
            ],
        ])

        loop_task = asyncio.create_task(
            runtime._agent_loop(agent, fake_runner),
        )

        msg = Message(
            id="msg-003",
            timestamp=datetime.now(timezone.utc),
            from_node="tyrant",
            to=agent.id,
            type="CHAT",
            content={"text": "检查任务"},
        )
        await api.send_message(caller_id="tyrant", message=msg)

        await asyncio.sleep(0.3)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        async with store.transaction():
            turns = await store.query_conversation_turns(agent.id)

        roles = [t.role for t in turns]
        # user + 中间model + tool_call + tool_result（final model 跳过）
        assert roles == ["user", "model", "tool_call", "tool_result"]

        # 中间 model 文本保留
        assert turns[1].content == "让我先查一下任务"

        # reply Message 包含 final response 文本
        async with store.transaction():
            tyrant_msgs = await store.query_messages(to_node="tyrant")
        replies = [m for m in tyrant_msgs if m.from_node == agent.id]
        assert len(replies) == 1
        assert replies[0].content["text"] == "任务列表为空"


# ── E2E: 查询 API 全链路 ──────────────────────────────────────────────────


class TestE2EQueryAPI:
    """通过 SystemAPI 查询消息和对话历史，验证权限和数据完整性。"""

    async def test_full_query_flow(self, stack):
        """
        完整流程：
        1. Tyrant → Agent 发消息
        2. 手动写入 turns（模拟 runtime）
        3. Tyrant 查询 Agent 的消息 ✓
        4. Tyrant 查询 Agent 的对话历史 ✓
        5. Agent 自查消息 ✓
        6. Agent 自查对话历史 ✓
        """
        store, api = stack["store"], stack["api"]
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )

        # Tyrant 发消息
        msg1 = Message(
            id="m1", timestamp=NOW,
            from_node="tyrant", to=agent.id,
            content={"text": "你好"},
        )
        await api.send_message(caller_id="tyrant", message=msg1)

        # Agent 回复
        msg2 = Message(
            id="m2", timestamp=NOW,
            from_node=agent.id, to="tyrant",
            content={"text": "你好，有什么需要？"},
        )
        await api.send_message(caller_id=agent.id, message=msg2)

        # 模拟 runtime 写入 turns
        async with store.transaction():
            await store.save_conversation_turn(ConversationTurn(
                id="t1", node_id=agent.id, role="user",
                content="你好", trigger_message_id="m1", timestamp=NOW,
            ))
            await store.save_conversation_turn(ConversationTurn(
                id="t2", node_id=agent.id, role="model",
                content="你好，有什么需要？", trigger_message_id="m1", timestamp=NOW,
            ))

        # --- Tyrant 查询 Agent 的消息 ---
        msgs = await api.query_messages(caller_id="tyrant", node_id=agent.id)
        assert len(msgs) == 2
        msg_ids = {m.id for m in msgs}
        assert "m1" in msg_ids
        assert "m2" in msg_ids

        # --- Tyrant 查询 Agent 的对话历史 ---
        turns = await api.query_conversation_history(
            caller_id="tyrant", target_id=agent.id,
        )
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[1].role == "model"

        # --- Agent 自查消息 ---
        self_msgs = await api.query_messages(
            caller_id=agent.id, node_id=agent.id,
        )
        assert len(self_msgs) == 2

        # --- Agent 自查对话历史 ---
        self_turns = await api.query_conversation_history(
            caller_id=agent.id, target_id=agent.id,
        )
        assert len(self_turns) == 2

    async def test_trigger_message_id_filtering(self, stack):
        """按 trigger_message_id 过滤对话历史。"""
        store, api = stack["store"], stack["api"]
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )

        # 两轮对话，不同 trigger
        async with store.transaction():
            await store.save_conversation_turn(ConversationTurn(
                id="t1", node_id=agent.id, role="user",
                content="第一轮", trigger_message_id="m1", timestamp=NOW,
            ))
            await store.save_conversation_turn(ConversationTurn(
                id="t2", node_id=agent.id, role="model",
                content="回复第一轮", trigger_message_id="m1", timestamp=NOW,
            ))
            await store.save_conversation_turn(ConversationTurn(
                id="t3", node_id=agent.id, role="user",
                content="第二轮", trigger_message_id="m2", timestamp=NOW,
            ))
            await store.save_conversation_turn(ConversationTurn(
                id="t4", node_id=agent.id, role="model",
                content="回复第二轮", trigger_message_id="m2", timestamp=NOW,
            ))

        # 只查第一轮
        turns = await api.query_conversation_history(
            caller_id="tyrant", target_id=agent.id,
            trigger_message_id="m1",
        )
        assert len(turns) == 2
        assert all(t.trigger_message_id == "m1" for t in turns)

        # 查全部
        all_turns = await api.query_conversation_history(
            caller_id="tyrant", target_id=agent.id,
        )
        assert len(all_turns) == 4

    async def test_message_type_filtering(self, stack):
        """按消息类型过滤。"""
        store, api = stack["store"], stack["api"]
        await _create_tyrant(store)
        agent = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )

        # 发送 CHAT 和 NOTIFICATION
        await api.send_message(
            caller_id="tyrant",
            message=Message(
                id="chat1", timestamp=NOW,
                from_node="tyrant", to=agent.id,
                type="CHAT", content={"text": "hi"},
            ),
        )
        await api.send_message(
            caller_id="tyrant",
            message=Message(
                id="notif1", timestamp=NOW,
                from_node="tyrant", to=agent.id,
                type="NOTIFICATION", content={"event": "test"},
            ),
        )

        # 只查 CHAT
        chats = await api.query_messages(
            caller_id="tyrant", node_id=agent.id, type="CHAT",
        )
        assert len(chats) == 1
        assert chats[0].type == "CHAT"

        # 只查 NOTIFICATION
        notifs = await api.query_messages(
            caller_id="tyrant", node_id=agent.id, type="NOTIFICATION",
        )
        assert len(notifs) == 1
        assert notifs[0].type == "NOTIFICATION"


# ── E2E: 权限检查全链路 ───────────────────────────────────────────────────


class TestE2EPermissions:
    """端到端权限场景。"""

    async def test_peer_cannot_query_each_other(self, stack):
        """同级 Agent 之间无法互相查消息/对话历史。"""
        store, api = stack["store"], stack["api"]
        await _create_tyrant(store)
        a1 = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )
        a2 = await api.create_agent(
            caller_id="tyrant", name="A2", parent_id="tyrant",
        )

        with pytest.raises(PermissionDeniedError):
            await api.query_messages(caller_id=a1.id, node_id=a2.id)
        with pytest.raises(PermissionDeniedError):
            await api.query_conversation_history(
                caller_id=a1.id, target_id=a2.id,
            )

    async def test_manager_queries_subordinate(self, stack):
        """
        Manager（有 create_agent 权限）创建 Worker →
        Manager 可以查 Worker 的消息和对话历史。
        """
        store, api = stack["store"], stack["api"]
        await _create_tyrant(store)
        manager = await api.create_agent(
            caller_id="tyrant", name="Manager", parent_id="tyrant",
        )
        # 授权 manager
        await api.grant_permission(
            caller_id="tyrant", target_id=manager.id,
            permission=Permission(
                action="create_agent",
                scope=Scope(type=ScopeType.SELF_SUBTREE),
            ),
        )
        worker = await api.create_agent(
            caller_id=manager.id, name="Worker", parent_id=manager.id,
        )

        # 写入 Worker 的对话历史
        async with store.transaction():
            await store.save_conversation_turn(ConversationTurn(
                id="t1", node_id=worker.id, role="user",
                content="任务", timestamp=NOW,
            ))

        # Manager 是 Worker 的直接上级 → 可查
        turns = await api.query_conversation_history(
            caller_id=manager.id, target_id=worker.id,
        )
        assert len(turns) == 1

        msgs = await api.query_messages(
            caller_id=manager.id, node_id=worker.id,
        )
        # Worker 还没有消息，但不应报权限错误
        assert msgs == []

    async def test_audit_permission_grants_cross_query(self, stack):
        """
        A1 获得 audit 权限 → 可以查 A2 的消息和对话历史。
        """
        store, api = stack["store"], stack["api"]
        await _create_tyrant(store)
        a1 = await api.create_agent(
            caller_id="tyrant", name="Auditor", parent_id="tyrant",
        )
        a2 = await api.create_agent(
            caller_id="tyrant", name="Target", parent_id="tyrant",
        )

        # 发消息给 a2
        await api.send_message(
            caller_id="tyrant",
            message=Message(
                id="m1", timestamp=NOW,
                from_node="tyrant", to=a2.id,
                content={"text": "hello target"},
            ),
        )
        async with store.transaction():
            await store.save_conversation_turn(ConversationTurn(
                id="t1", node_id=a2.id, role="user",
                content="hello", timestamp=NOW,
            ))

        # 无权限 → 拒绝
        with pytest.raises(PermissionDeniedError):
            await api.query_messages(caller_id=a1.id, node_id=a2.id)

        # 授予 audit 权限
        await api.grant_permission(
            caller_id="tyrant", target_id=a1.id,
            permission=Permission(
                action="audit",
                effect=PermissionEffect.ALLOW,
                scope=Scope(type=ScopeType.GLOBAL),
            ),
        )

        # 现在可以查了
        msgs = await api.query_messages(caller_id=a1.id, node_id=a2.id)
        assert len(msgs) == 1

        turns = await api.query_conversation_history(
            caller_id=a1.id, target_id=a2.id,
        )
        assert len(turns) == 1

    async def test_scoped_audit_restricts_target(self, stack):
        """
        audit 权限如果是 NODES 作用域，只能查指定节点。
        """
        store, api = stack["store"], stack["api"]
        await _create_tyrant(store)
        auditor = await api.create_agent(
            caller_id="tyrant", name="Auditor", parent_id="tyrant",
        )
        target_ok = await api.create_agent(
            caller_id="tyrant", name="TargetOK", parent_id="tyrant",
        )
        target_no = await api.create_agent(
            caller_id="tyrant", name="TargetNo", parent_id="tyrant",
        )

        # 只授权 audit 针对 target_ok
        await api.grant_permission(
            caller_id="tyrant", target_id=auditor.id,
            permission=Permission(
                action="audit",
                scope=Scope(type=ScopeType.NODES, targets=[target_ok.id]),
            ),
        )

        # target_ok → 可查
        await api.query_messages(
            caller_id=auditor.id, node_id=target_ok.id,
        )

        # target_no → 拒绝
        with pytest.raises(PermissionDeniedError):
            await api.query_messages(
                caller_id=auditor.id, node_id=target_no.id,
            )
