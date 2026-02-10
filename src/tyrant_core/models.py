"""零依赖模块。所有实体的 Pydantic 模型定义 + Enum + 异常。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────


class NodeType(str, Enum):
    HUMAN = "HUMAN"
    LOCAL_AGENT = "LOCAL_AGENT"
    EXTERNAL_AGENT = "EXTERNAL_AGENT"


class NodeStatus(str, Enum):
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    OFFLINE = "OFFLINE"
    SUSPENDED = "SUSPENDED"


class RelationType(str, Enum):
    REPORTS_TO = "REPORTS_TO"
    CONNECTED = "CONNECTED"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Priority(int, Enum):
    SYSTEM = 0
    URGENT = 1
    NORMAL = 2
    LOW = 3


class RefType(str, Enum):
    MESSAGE = "MESSAGE"
    TASK = "TASK"
    NODE = "NODE"


# ── 嵌套结构 ───────────────────────────────────────────────────────────────


class Endpoint(BaseModel):
    protocol: str  # "local" / "adapter"
    address: str  # 地址或适配器ID
    config: dict = {}  # 适配器特有配置


class Permissions(BaseModel):
    create_agent: bool = False
    destroy_agent: bool = False
    recruit: bool = False
    audit: bool = False
    grant_permission: bool = False


class Reference(BaseModel):
    ref_type: RefType
    ref_id: str
    relation: str  # "reply_to" / "origin" / "parent" / "attachment" / "related"


# ── 核心实体 ───────────────────────────────────────────────────────────────


class Node(BaseModel):
    id: str  # uuid4
    type: NodeType
    name: str
    is_owner: bool = False
    endpoint: Endpoint
    status: NodeStatus = NodeStatus.OFFLINE
    permissions: Permissions = Field(default_factory=Permissions)
    metadata: dict = {}
    created_at: datetime


class Relation(BaseModel):
    id: str  # uuid4
    type: RelationType
    node_a: str  # REPORTS_TO: 上级; CONNECTED: 任意
    node_b: str  # REPORTS_TO: 下级
    config: dict = {}
    created_at: datetime


class Message(BaseModel):
    id: str  # uuid4
    timestamp: datetime
    expires_at: datetime | None = None
    from_node: str  # 发送者节点ID
    to: str | list[str]  # 接收者
    type: str = "CHAT"
    priority: Priority = Priority.NORMAL
    content: dict = {}
    references: list[Reference] = []
    thread_id: str | None = None
    requires_ack: bool = False

    model_config = {
        "populate_by_name": True,
    }


class Task(BaseModel):
    id: str  # uuid4
    creator: str
    assignee: str
    participants: list[str] = []
    status: TaskStatus = TaskStatus.PENDING
    content: dict = {}
    parent_task: str | None = None
    references: list[Reference] = []
    created_at: datetime
    updated_at: datetime


class Event(BaseModel):
    id: str  # uuid4
    type: str  # "node.status_changed" / 自定义
    timestamp: datetime
    source: str  # 节点ID 或 "system"
    data: dict = {}


# ── 异常 ───────────────────────────────────────────────────────────────────


class TyrantError(Exception):
    """TyrantCore 所有自定义异常的基类。"""


class NodeNotFoundError(TyrantError):
    pass


class RelationNotFoundError(TyrantError):
    pass


class MessageNotFoundError(TyrantError):
    pass


class TaskNotFoundError(TyrantError):
    pass


class PermissionDeniedError(TyrantError):
    pass


class RoutingError(TyrantError):
    pass


class ValidationError(TyrantError):
    pass
