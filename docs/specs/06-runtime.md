# 06 - Agent 运行时 (runtime/agent_runtime.py)

依赖：system_api, message_bus, event_bus, google-adk, models

## 职责

- 管理 LOCAL_AGENT 的生命周期（启动、停止、恢复）
- 将 TyrantCore 系统 API 暴露为 ADK tools
- 桥接 inbox 消息 → ADK agent 调用
- 管理 Agent 记忆文件的读写

## 接口

```python
class AgentRuntime:
    def __init__(self, api: SystemAPI, message_bus: MessageBus, event_bus: EventBus): ...

    async def start_agent(self, node: Node) -> None:
        """启动一个 Agent（创建 ADK Agent 实例 + 启动 inbox 监听协程）"""

    async def stop_agent(self, node_id: str) -> None:
        """停止一个 Agent（取消协程 + 清理内存状态）"""

    async def recover(self) -> None:
        """系统重启：从 store 恢复所有 ONLINE 的 LOCAL_AGENT 并启动"""

    async def shutdown(self) -> None:
        """优雅关闭所有 Agent"""
```

## ADK Agent 创建

每个 LOCAL_AGENT 对应一个 ADK `LlmAgent` + `Runner` 实例：

```python
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

APP_NAME = "tyrant_core"

def _create_adk_agent(self, node: Node) -> LlmAgent:
    config = node.metadata.get("agent_config", {})
    return LlmAgent(
        model=config.get("model", "gemini-2.0-flash"),
        name=node.name,
        description=config.get("description", f"Agent {node.name}"),
        instruction=config.get("instruction", "You are a helpful agent."),
        tools=self._build_tools(node.id),
    )

def _create_runner(self, agent: LlmAgent) -> Runner:
    return Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=self._session_service,
    )
```

## Tool 暴露

TyrantCore 的系统 API 作为 Python 函数暴露给 ADK Agent：

```python
def _build_tools(self, agent_id: str) -> list[Callable]:
    """为指定 Agent 构建可用的 tool 函数列表"""

    async def send_message(to: str, type: str, content: dict) -> dict:
        """向指定节点发送消息"""
        msg = Message(to=to, type=type, content=content, ...)
        ids = await self.api.send_message(agent_id, msg)
        return {"sent": len(ids), "message_ids": ids}

    async def create_task(assignee: str, content: dict, parent_task: str = None) -> dict:
        """创建新任务"""
        task = Task(creator=agent_id, assignee=assignee, content=content, ...)
        result = await self.api.task_create(agent_id, task)
        return {"task_id": result.id}

    async def update_task(task_id: str, status: str = None, content: dict = None) -> dict:
        """更新任务状态或内容"""
        fields = {}
        if status: fields["status"] = status
        if content: fields["content"] = content
        result = await self.api.task_update(agent_id, task_id, **fields)
        return {"task_id": result.id, "status": result.status}

    async def query_tasks(assignee: str = None, status: str = None) -> dict:
        """查询任务"""
        tasks = await self.api.task_query(agent_id, assignee=assignee, status=status)
        return {"tasks": [t.model_dump() for t in tasks]}

    async def create_agent(name: str, instruction: str, model: str = "gemini-2.0-flash") -> dict:
        """创建新的下属 Agent"""
        node = await self.api.create_agent(
            caller_id=agent_id,
            name=name,
            parent_id=agent_id,  # 新 Agent 的上级是调用者自己
            agent_config={"model": model, "instruction": instruction},
        )
        await self.start_agent(node)
        return {"agent_id": node.id, "name": node.name}

    async def release_agent(agent_id_to_release: str) -> dict:
        """释放下属 Agent 到人才池"""
        await self.api.talent_pool_release(agent_id, agent_id_to_release)
        await self.stop_agent(agent_id_to_release)
        return {"released": agent_id_to_release}

    async def query_talent_pool() -> dict:
        """查询人才池"""
        nodes = await self.api.talent_pool_query(agent_id)
        return {"agents": [{"id": n.id, "name": n.name, "metadata": n.metadata} for n in nodes]}

    async def recruit_agent(target_id: str) -> dict:
        """从人才池招募 Agent"""
        rel = await self.api.talent_pool_recruit(agent_id, target_id, parent_id=agent_id)
        node = await self.api.store.get_node(target_id)
        await self.start_agent(node)
        return {"recruited": target_id}

    return [
        send_message, create_task, update_task, query_tasks,
        create_agent, release_agent, query_talent_pool, recruit_agent,
    ]
```

## Inbox → ADK 桥接

每个 Agent 一个 async task 持续监听 inbox，通过 Runner.run_async() 调用：

```python
async def _agent_loop(self, node: Node):
    adk_agent = self._create_adk_agent(node)
    runner = self._create_runner(adk_agent)
    user_id = node.id
    session_id = f"session_{node.id}"

    # 创建 ADK session
    session = await self._session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id,
    )

    async for message in self.message_bus.subscribe_inbox(node.id):
        try:
            # 将 Message 转为 ADK Content
            user_content = self._format_message_for_adk(message)

            # 通过 Runner 调用 Agent
            response_text = ""
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    response_text = event.content.parts[0].text

            # ACK
            if message.requires_ack:
                await self.message_bus.ack(message.id, node.id)

        except Exception:
            logger.exception(f"Agent {node.id} failed processing message {message.id}")
            await self.event_bus.publish(Event(type="agent.crashed", source=node.id, ...))
```

## 消息格式转换

```python
def _format_message_for_adk(self, message: Message) -> types.Content:
    """将 TyrantCore Message 转为 ADK Content 对象"""
    parts = [f"[From: {message.from_node}] [Type: {message.type}]"]
    if message.content:
        parts.append(json.dumps(message.content, ensure_ascii=False))
    text = "\n".join(parts)
    return types.Content(role="user", parts=[types.Part(text=text)])
```

## 内存结构

```python
self._session_service = InMemorySessionService()  # v1 用内存，v2 可换 DatabaseSessionService
self._agents: dict[str, asyncio.Task] = {}        # node_id → agent_loop task
self._runners: dict[str, Runner] = {}             # node_id → Runner 实例
```

## Agent 记忆

- ADK Session 管理对话历史（v1 用 InMemorySessionService，重启丢失）
- 额外记忆文件: `data/agents/{node_id}/memory.json`（Runtime 管理）
- v2 可切换为 `DatabaseSessionService("sqlite+aiosqlite:///data/tyrant.db")` 实现持久化
