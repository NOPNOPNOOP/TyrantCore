"""v2 核心领域模型。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ActorKind(str, Enum):
    TYRANT = "TYRANT"
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SERVICE = "SERVICE"
    TERMINAL = "TERMINAL"
    EXTERNAL = "EXTERNAL"


class OrgEdgeKind(str, Enum):
    REPORTS_TO = "REPORTS_TO"
    MEMBER_OF = "MEMBER_OF"


class GrantEffect(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class GrantScopeType(str, Enum):
    GLOBAL = "GLOBAL"
    SUBTREE = "SUBTREE"
    ACTORS = "ACTORS"
    THREADS = "THREADS"
    WORK_ITEMS = "WORK_ITEMS"


class GrantSourceType(str, Enum):
    DIRECT = "DIRECT"
    DELEGATED = "DELEGATED"
    IMPLICIT = "IMPLICIT"
    SYSTEM = "SYSTEM"


class ThreadKind(str, Enum):
    DIALOG = "DIALOG"
    TASK = "TASK"
    SYSTEM = "SYSTEM"
    INCIDENT = "INCIDENT"


class Priority(int, Enum):
    SYSTEM = 0
    URGENT = 1
    NORMAL = 2
    LOW = 3


class ReferenceType(str, Enum):
    ACTOR = "ACTOR"
    THREAD = "THREAD"
    ENVELOPE = "ENVELOPE"
    DELIVERY = "DELIVERY"
    WORK_ITEM = "WORK_ITEM"


class DeliveryState(str, Enum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    ACKED = "ACKED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class BindingThreadPolicy(str, Enum):
    REUSE = "REUSE"
    NEW_PER_MESSAGE = "NEW_PER_MESSAGE"
    BY_PEER = "BY_PEER"
    BY_TASK = "BY_TASK"


class SessionMode(str, Enum):
    PERSISTENT = "PERSISTENT"
    EPHEMERAL = "EPHEMERAL"


class SessionState(str, Enum):
    OPEN = "OPEN"
    IDLE = "IDLE"
    CLOSED = "CLOSED"


class AttachmentKind(str, Enum):
    LOCAL_RUNTIME = "LOCAL_RUNTIME"
    TERMINAL = "TERMINAL"
    UI = "UI"
    CONNECTOR = "CONNECTOR"
    WEBHOOK = "WEBHOOK"


class AttachmentDesiredState(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DETACHED = "DETACHED"


class PresenceStatus(str, Enum):
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class WorkItemStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class ProvisionStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class DesiredState(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DETACHED = "DETACHED"
    ARCHIVED = "ARCHIVED"


class ActivationPolicy(str, Enum):
    MANUAL = "MANUAL"
    AUTO_START = "AUTO_START"
    LAZY = "LAZY"


class RuntimeKind(str, Enum):
    LOCAL = "LOCAL"
    TERMINAL = "TERMINAL"
    REMOTE = "REMOTE"
    EXTERNAL = "EXTERNAL"


class Reference(BaseModel):
    ref_type: ReferenceType
    ref_id: str
    relation: str


class GrantScope(BaseModel):
    type: GrantScopeType = GrantScopeType.GLOBAL
    targets: list[str] = Field(default_factory=list)


class Actor(BaseModel):
    id: str
    kind: ActorKind
    name: str
    slug: str
    org_role: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    archived_at: datetime | None = None


class OrgEdge(BaseModel):
    id: str
    kind: OrgEdgeKind
    src_actor_id: str
    dst_actor_id: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    ended_at: datetime | None = None


class Grant(BaseModel):
    id: str
    subject_actor_id: str
    action: str
    effect: GrantEffect = GrantEffect.ALLOW
    scope: GrantScope = Field(default_factory=GrantScope)
    source_type: GrantSourceType = GrantSourceType.DIRECT
    source_actor_id: str | None = None
    delegable: bool = False
    expires_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None


class Thread(BaseModel):
    id: str
    kind: ThreadKind = ThreadKind.DIALOG
    subject: str = ""
    created_by: str
    participants: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    closed_at: datetime | None = None


class Envelope(BaseModel):
    id: str
    thread_id: str
    sender_actor_id: str
    kind: str = "CHAT"
    payload: dict = Field(default_factory=dict)
    priority: Priority = Priority.NORMAL
    correlation_id: str | None = None
    idempotency_key: str | None = None
    references: list[Reference] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime | None = None


class Delivery(BaseModel):
    id: str
    envelope_id: str
    recipient_actor_id: str
    target_attachment_id: str | None = None
    state: DeliveryState = DeliveryState.PENDING
    attempt_count: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    delivered_at: datetime | None = None
    acked_at: datetime | None = None
    next_retry_at: datetime | None = None
    last_error: dict | None = None
    created_at: datetime


class Binding(BaseModel):
    id: str
    surface: str
    account_id: str | None = None
    peer: str | None = None
    actor_id: str
    thread_policy: BindingThreadPolicy = BindingThreadPolicy.REUSE
    fixed_thread_id: str | None = None
    priority: int = 0
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    disabled_at: datetime | None = None


class Session(BaseModel):
    id: str
    surface: str
    binding_id: str | None = None
    actor_id: str
    external_key: str
    thread_id: str | None = None
    mode: SessionMode = SessionMode.PERSISTENT
    state: SessionState = SessionState.OPEN
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None = None


class Attachment(BaseModel):
    id: str
    actor_id: str
    kind: AttachmentKind
    endpoint: dict = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    desired_state: AttachmentDesiredState = AttachmentDesiredState.ACTIVE
    created_at: datetime
    detached_at: datetime | None = None


class Presence(BaseModel):
    attachment_id: str
    actor_id: str
    gateway_id: str
    status: PresenceStatus = PresenceStatus.ONLINE
    leases: int = 0
    last_heartbeat_at: datetime


class WorkItem(BaseModel):
    id: str
    kind: str = "TASK"
    creator_actor_id: str
    owner_actor_id: str
    thread_id: str
    parent_work_item_id: str | None = None
    status: WorkItemStatus = WorkItemStatus.PENDING
    spec: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class ActorBlueprint(BaseModel):
    name: str
    kind: ActorKind = ActorKind.AGENT
    parent_actor_id: str
    org_role: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    profile_id: str | None = None
    manager_actor_id: str | None = None
    grant_template: str | None = None
    bootstrap_spec: dict = Field(default_factory=dict)
    runtime_spec: dict = Field(default_factory=dict)


class ProvisionRequest(BaseModel):
    id: str
    requested_by_actor_id: str
    target_parent_actor_id: str
    manager_actor_id: str | None = None
    profile_id: str | None = None
    provisioner_hint: str | None = None
    priority: int = 0
    status: ProvisionStatus = ProvisionStatus.PENDING
    idempotency_key: str | None = None
    reason: str | None = None
    blueprint: ActorBlueprint
    work_item_id: str | None = None
    claimed_by_actor_id: str | None = None
    claimed_at: datetime | None = None
    created_at: datetime
    closed_at: datetime | None = None


class ProvisionRecord(BaseModel):
    id: str
    request_id: str
    provisioner_name: str
    executor_actor_id: str | None = None
    produced_actor_id: str | None = None
    org_edge_id: str | None = None
    control_id: str | None = None
    bootstrap_thread_id: str | None = None
    attachment_id: str | None = None
    state: ProvisionStatus = ProvisionStatus.RUNNING
    attempt_count: int = 0
    last_error: dict | None = None
    notes: dict = Field(default_factory=dict)
    created_at: datetime
    finished_at: datetime | None = None


class ActorProfile(BaseModel):
    id: str
    name: str
    instruction: str = ""
    toolsets: list[str] = Field(default_factory=list)
    model_ref: str | None = None
    session_mode: SessionMode = SessionMode.PERSISTENT
    runtime_kind: RuntimeKind = RuntimeKind.LOCAL
    memory_policy: dict = Field(default_factory=dict)
    bootstrap_policy: dict = Field(default_factory=dict)
    created_at: datetime
    archived_at: datetime | None = None


class ActorControl(BaseModel):
    id: str
    actor_id: str
    manager_actor_id: str
    profile_id: str | None = None
    desired_state: DesiredState = DesiredState.ACTIVE
    activation_policy: ActivationPolicy = ActivationPolicy.AUTO_START
    runtime_kind: RuntimeKind = RuntimeKind.LOCAL
    placement: dict = Field(default_factory=dict)
    limits: dict = Field(default_factory=dict)
    updated_at: datetime


class DomainEvent(BaseModel):
    id: str
    type: str
    source: str
    payload: dict = Field(default_factory=dict)
    created_at: datetime


class TyrantError(Exception):
    """TyrantCore v2 所有自定义异常的基类。"""


class ValidationError(TyrantError):
    pass


class ActorNotFoundError(TyrantError):
    pass


class OrgEdgeNotFoundError(TyrantError):
    pass


class GrantNotFoundError(TyrantError):
    pass


class ThreadNotFoundError(TyrantError):
    pass


class EnvelopeNotFoundError(TyrantError):
    pass


class DeliveryNotFoundError(TyrantError):
    pass


class WorkItemNotFoundError(TyrantError):
    pass


class ActorProfileNotFoundError(TyrantError):
    pass


class ActorControlNotFoundError(TyrantError):
    pass


class ProvisionRequestNotFoundError(TyrantError):
    pass


class ProvisionRecordNotFoundError(TyrantError):
    pass


class PolicyDeniedError(TyrantError):
    pass


class BindingResolutionError(TyrantError):
    pass


class UnsupportedLegacyComponentError(TyrantError):
    pass
