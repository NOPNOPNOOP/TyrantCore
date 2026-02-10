# 07 - CLI 适配器 (adapters/cli.py)

依赖：system_api, message_bus, models, rich, prompt_toolkit

## 职责

- 提供终端交互界面，供 Tyrant（人类）与系统交互
- 使用 prompt_toolkit 实现异步输入（命令补全、历史记录）
- 使用 rich 实现美化输出（彩色、表格、Panel）
- 将用户终端输入转为 Message 发送给指定 Agent
- 监听 Tyrant 节点的 inbox，实时显示收到的消息

## 接口

```python
class CliAdapter:
    def __init__(self, api: SystemAPI, message_bus: MessageBus, tyrant_node_id: str): ...

    async def run(self) -> None:
        """主循环：并行运行输入处理和消息接收"""

    async def _input_loop(self) -> None:
        """读取终端输入，解析并执行"""

    async def _receive_loop(self) -> None:
        """监听 Tyrant inbox，显示收到的消息"""
```

## 交互模式

v1 采用简单的命令行模式：

```
TyrantCore> /talk top_agent 写一个爬虫项目
TyrantCore> /status
TyrantCore> /inbox
TyrantCore> 写一个爬虫项目          # 默认发给当前对话目标
```

## 命令

| 命令 | 作用 |
|------|------|
| `/talk <node_id> <text>` | 向指定节点发送 CHAT 消息 |
| `/target <node_id>` | 设置默认对话目标（后续直接输入文本即发送） |
| `/inbox` | 查看 Tyrant 的收件箱 |
| `/nodes` | 列出所有节点 |
| `/relations` | 列出所有关系 |
| `/tasks` | 列出所有任务 |
| `/create <name> <instruction>` | 手动创建 Agent |
| `/grant <node_id> <permission>` | 授予权限 |
| `/inspect <node_id>` | 审计查看节点状态 |
| `/events [since]` | 查看事件日志 |
| `/help` | 显示帮助 |
| `/quit` | 退出 |

无前缀的文本 → 发送给当前 target 的 CHAT 消息。

## 输入处理

使用 prompt_toolkit 的异步 prompt 替代裸 `input()`：

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

COMMANDS = ["/talk", "/target", "/inbox", "/nodes", "/relations",
            "/tasks", "/create", "/grant", "/inspect", "/events",
            "/help", "/quit"]

class CliAdapter:
    def __init__(self, api: SystemAPI, message_bus: MessageBus, tyrant_node_id: str):
        self.api = api
        self.message_bus = message_bus
        self.tyrant_node_id = tyrant_node_id
        self.console = Console()
        self.prompt_session = PromptSession(
            completer=WordCompleter(COMMANDS, sentence=True),
        )
        self.target = None  # 当前对话目标

async def _input_loop(self):
    while True:
        try:
            line = await self.prompt_session.prompt_async("TyrantCore> ")
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue

        if line.startswith("/"):
            await self._handle_command(line)
        elif self.target:
            msg = Message(
                to=self.target,
                type="CHAT",
                content={"text": line},
                priority=Priority.NORMAL,
            )
            await self.api.send_message(self.tyrant_node_id, msg)
        else:
            self.console.print("[请先用 /target <node_id> 设置对话目标]", style="yellow")
```

## 消息显示

使用 rich 美化输出：

```python
async def _receive_loop(self):
    async for message in self.message_bus.subscribe_inbox(self.tyrant_node_id):
        self._display_message(message)
        if message.requires_ack:
            await self.message_bus.ack(message.id, self.tyrant_node_id)

def _display_message(self, message: Message):
    """用 rich 格式化消息输出"""
    title = f"📨 [{message.type}] from {message.from_node}"
    if "text" in message.content:
        body = message.content["text"]
    else:
        body = json.dumps(message.content, ensure_ascii=False, indent=2)
    self.console.print(Panel(body, title=title, border_style="cyan"))
```

## 启动时的初始化

```python
async def run(self):
    # 确保 Tyrant 节点存在
    tyrant = await self.api.store.get_node(self.tyrant_node_id)
    if not tyrant:
        # 首次启动，创建 Tyrant 节点
        tyrant = Node(
            id=self.tyrant_node_id,
            type=NodeType.HUMAN,
            name="Tyrant",
            is_owner=True,
            endpoint=Endpoint(protocol="cli", address="local"),
            status=NodeStatus.ONLINE,
            permissions=Permissions(
                create_agent=True, destroy_agent=True,
                recruit=True, audit=True, grant_permission=True,
            ),
        )
        await self.api.store.save_node(tyrant)

    self.console.print(Panel(
        "TyrantCore v0.1 — 输入 /help 查看命令",
        style="bold green",
    ))

    # 并行运行输入和接收
    await asyncio.gather(
        self._input_loop(),
        self._receive_loop(),
    )
```
