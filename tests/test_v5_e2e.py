"""v5 端到端测试：服务发现 + Agent Creator + per_request + clone_node。"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tyrant_core.models import (
    Endpoint,
    Node,
    NodeStatus,
    NodeType,
    Permission,
    Scope,
    ScopeType,
)
from tyrant_core.store.sqlite_store import SqliteStore
from tyrant_core.bus.event_bus import EventBus
from tyrant_core.bus.message_bus import MessageBus
from tyrant_core.api.system_api import SystemAPI

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    s = SqliteStore(engine)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def event_bus(store):
    return EventBus(store)


@pytest.fixture
def message_bus(store, event_bus):
    return MessageBus(store, event_bus)


@pytest.fixture
def api(store, message_bus, event_bus):
    return SystemAPI(store, message_bus, event_bus)


async def _setup_tyrant(store):
    tyrant = Node(
        id="tyrant", type=NodeType.HUMAN, name="Tyrant",
        is_owner=True, endpoint=Endpoint(protocol="cli", address="local"),
        status=NodeStatus.ONLINE, created_at=NOW,
    )
    async with store.transaction():
        await store.save_node(tyrant)


class TestAgentCreatorE2E:
    """模拟完整的 Agent Creator 创建流程。"""

    async def test_create_agent_creator_with_tags_and_permissions(
        self, store, api,
    ):
        """模拟 CLI 启动时创建 Agent Creator 的完整流程。"""
        await _setup_tyrant(store)

        # 1. 创建 Agent Creator 节点
        creator = await api.create_agent(
            caller_id="tyrant",
            name="Agent Creator",
            parent_id="tyrant",
            agent_config={
                "instruction": "你是 Agent Creator",
                "session_mode": "per_request",
            },
        )
        assert creator.status == NodeStatus.ONLINE

        # 2. 设置 tags
        async with store.transaction():
            await store.update_node(creator.id, tags=["agent_creator"])

        # 3. 授予 global 权限
        await api.grant_permission(
            "tyrant", creator.id,
            Permission(action="create_agent", scope=Scope(type=ScopeType.GLOBAL)),
        )
        await api.grant_permission(
            "tyrant", creator.id,
            Permission(action="grant_permission", scope=Scope(type=ScopeType.GLOBAL)),
        )

        # 4. 验证 tags 持久化
        async with store.transaction():
            node = await store.get_node(creator.id)
        assert node.tags == ["agent_creator"]

        # 5. 验证权限
        assert len(node.permissions) == 2
        actions = {p.action for p in node.permissions}
        assert actions == {"create_agent", "grant_permission"}

    async def test_service_discovery_by_tags(self, store, api):
        """模拟 Agent 通过 list_nodes 发现系统服务。"""
        await _setup_tyrant(store)

        # 创建多个节点，只有一个有 tag
        a1 = await api.create_agent(
            caller_id="tyrant", name="Worker", parent_id="tyrant",
        )
        creator = await api.create_agent(
            caller_id="tyrant", name="Agent Creator", parent_id="tyrant",
            agent_config={"session_mode": "per_request"},
        )
        async with store.transaction():
            await store.update_node(creator.id, tags=["agent_creator"])

        # 服务发现：按 tag 查找
        found = await api.list_nodes(
            caller_id=a1.id, tags=["agent_creator"],
        )
        assert len(found) == 1
        assert found[0].name == "Agent Creator"

        # 无 tag 过滤：返回所有
        all_nodes = await api.list_nodes(caller_id=a1.id)
        assert len(all_nodes) >= 3  # tyrant + Worker + Agent Creator


class TestPerRequestSession:
    """验证 per_request session 模式的配置正确性。"""

    async def test_session_mode_in_agent_config(self, store, api):
        await _setup_tyrant(store)

        node = await api.create_agent(
            caller_id="tyrant", name="Stateless",
            parent_id="tyrant",
            agent_config={"session_mode": "per_request"},
        )

        async with store.transaction():
            got = await store.get_node(node.id)
        assert got.metadata["agent_config"]["session_mode"] == "per_request"

    async def test_default_session_mode_is_persistent(self, store, api):
        await _setup_tyrant(store)

        node = await api.create_agent(
            caller_id="tyrant", name="Normal",
            parent_id="tyrant",
        )

        async with store.transaction():
            got = await store.get_node(node.id)
        config = got.metadata.get("agent_config", {})
        # 默认不设置 session_mode，runtime 会默认 persistent
        assert config.get("session_mode") is None


class TestCloneNodeE2E:
    """验证 clone_node 完整流程。"""

    async def test_clone_preserves_all_attributes(self, store, api):
        """克隆应复制 config、permissions、tags。"""
        await _setup_tyrant(store)

        # 创建模板节点
        template = await api.create_agent(
            caller_id="tyrant", name="Template",
            parent_id="tyrant",
            agent_config={
                "instruction": "模板指令",
                "session_mode": "per_request",
                "model": "gpt-4",
            },
        )
        async with store.transaction():
            await store.update_node(template.id, tags=["worker", "v2"])
        await api.grant_permission(
            "tyrant", template.id,
            Permission(action="create_agent", scope=Scope(type=ScopeType.SELF_SUBTREE)),
        )

        # 克隆
        clone = await api.clone_node(
            caller_id="tyrant",
            source_id=template.id,
            parent_id="tyrant",
            name="Clone-1",
        )

        # 验证基本属性
        assert clone.name == "Clone-1"
        assert clone.type == NodeType.LOCAL_AGENT
        assert clone.status == NodeStatus.ONLINE
        assert clone.id != template.id

        # 验证 tags 复制
        assert clone.tags == ["worker", "v2"]

        # 验证 agent_config 复制
        config = clone.metadata.get("agent_config", {})
        assert config["instruction"] == "模板指令"
        assert config["session_mode"] == "per_request"
        assert config["model"] == "gpt-4"

        # 验证 permissions 复制
        assert len(clone.permissions) == 1
        assert clone.permissions[0].action == "create_agent"

        # 验证组织关系
        async with store.transaction():
            rel = await store.get_reports_to(clone.id)
        assert rel is not None
        assert rel.node_a == "tyrant"

    async def test_clone_default_name(self, store, api):
        """不指定名称时使用 source.name-clone。"""
        await _setup_tyrant(store)
        source = await api.create_agent(
            caller_id="tyrant", name="MyAgent", parent_id="tyrant",
        )
        clone = await api.clone_node(
            caller_id="tyrant", source_id=source.id, parent_id="tyrant",
        )
        assert clone.name == "MyAgent-clone"

    async def test_clone_creates_connected_relations(self, store, api):
        """克隆节点应与同级节点自动建立 CONNECTED。"""
        await _setup_tyrant(store)
        a1 = await api.create_agent(
            caller_id="tyrant", name="A1", parent_id="tyrant",
        )
        source = await api.create_agent(
            caller_id="tyrant", name="Source", parent_id="tyrant",
        )

        clone = await api.clone_node(
            caller_id="tyrant", source_id=source.id, parent_id="tyrant",
        )

        # clone 应与 a1、source 都有 CONNECTED
        async with store.transaction():
            rels = await store.get_relations_for_node(clone.id)
        connected = [r for r in rels if r.type.value == "CONNECTED"]
        # 至少与 a1 和 source 有 CONNECTED
        peer_ids = set()
        for r in connected:
            peer_ids.add(r.node_a if r.node_b == clone.id else r.node_b)
        assert a1.id in peer_ids
        assert source.id in peer_ids
