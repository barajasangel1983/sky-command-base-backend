from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


# Auth / Users

class User(BaseModel):
    id: str
    username: str
    role: Literal["admin", "operator"]
    status: Literal["active", "inactive"]
    createdAt: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: Literal["admin", "operator"]


class UserUpdateRole(BaseModel):
    role: Literal["admin", "operator"]


class TokenResponse(BaseModel):
    token: str
    user: User


class LoginRequest(BaseModel):
    username: str
    password: str


# Kanban

KanbanColumn = Literal["parking", "defined", "in_progress", "blocked", "done"]
KanbanPriority = Literal["low", "medium", "high"]


class KanbanCard(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    column: KanbanColumn
    priority: KanbanPriority
    createdAt: str


class KanbanCardCreate(BaseModel):
    title: str
    description: Optional[str] = None
    column: KanbanColumn = "parking"
    priority: KanbanPriority = "medium"


class KanbanMoveRequest(BaseModel):
    column: KanbanColumn


# Data export / system health

class ExportJob(BaseModel):
    id: str
    type: Literal["data", "logs"]
    status: Literal["complete", "pending", "failed"]
    createdAt: datetime
    url: Optional[str] = None


class SystemHealth(BaseModel):
    cpu: int
    memory: int
    disk: int
    uptime: str


# IoT

class IoTDevice(BaseModel):
    id: str
    name: str
    type: str
    status: Literal["online", "offline"]
    enabled: bool
    value: Optional[str] = None
    location: str


# Finance

class Transaction(BaseModel):
    id: str
    date: str
    description: str
    amount: float
    category: str
    type: Literal["income", "expense"]


class MonthlySpend(BaseModel):
    month: str
    amount: float


# Files

class FileEntry(BaseModel):
    id: str
    name: str
    size: int
    type: str
    uploadedAt: str
    uploadedBy: str


# ClaudeBot Monitor

class AgentSessionSchema(BaseModel):
    id: str
    startedAt: str
    endedAt: Optional[str] = None
    model: str
    channel: str
    tokensIn: int
    tokensOut: int
    status: Literal["running", "completed", "error"]
    summary: Optional[str] = None


class AgentSessionCreate(BaseModel):
    id: Optional[str] = None
    model: str
    channel: str
    tokens_in: int = 0
    tokens_out: int = 0
    status: Literal["running", "completed", "error"] = "running"
    summary: Optional[str] = None


class AgentSessionUpdate(BaseModel):
    status: Optional[Literal["running", "completed", "error"]] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    summary: Optional[str] = None


class TimelineEventSchema(BaseModel):
    id: str
    sessionId: str
    type: str
    timestamp: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class TimelineEventCreate(BaseModel):
    type: Literal["prompt", "tool_start", "tool_end", "response", "error"]
    content: str
    metadata: Optional[Dict[str, Any]] = None


class ToolCallSchema(BaseModel):
    id: str
    sessionId: str
    name: str
    input: str
    output: str
    startedAt: str
    endedAt: str
    status: Literal["success", "error"]


class ToolCallCreate(BaseModel):
    name: str
    input: str
    output: str
    status: Literal["success", "error"] = "success"


class PromptSchema(BaseModel):
    id: str
    sessionId: str
    role: Literal["user", "system", "assistant"]
    content: str
    timestamp: str
    tokens: int


class PromptCreate(BaseModel):
    role: Literal["user", "system", "assistant"]
    content: str
    tokens: int = 0


# ---------------------------------------------------------------------------
# Other Research
# ---------------------------------------------------------------------------

OtherActionType = Literal["Research", "Comment", "Forward"]
OtherPriority = Literal["low", "medium", "high"]


class OtherResearchConfigSchema(BaseModel):
    topic: str
    setBy: str
    updatedAt: str
    enabled: bool


class OtherResearchConfigUpdate(BaseModel):
    topic: Optional[str] = None
    set_by: Optional[str] = None
    enabled: Optional[bool] = None


class OtherResearchReportSchema(BaseModel):
    id: str
    title: str
    source: str
    date: str
    score: int
    link: str
    topic: str
    createdAt: str


class OtherResearchReportCreate(BaseModel):
    title: str
    source: str
    date: str
    score: int = 0
    link: str = "#"
    topic: str


class OtherIdeaDropSchema(BaseModel):
    id: str
    title: str
    hook: str
    category: str
    priority: OtherPriority
    tags: List[str]
    createdBy: str
    createdAt: str


class OtherIdeaDropCreate(BaseModel):
    title: str
    hook: str
    category: str
    priority: OtherPriority = "medium"
    tags: List[str] = []


class OtherNextActionSchema(BaseModel):
    id: str
    label: str
    actionType: OtherActionType
    completed: bool
    createdAt: str


class OtherNextActionCreate(BaseModel):
    label: str
    action_type: OtherActionType = "Research"


class OtherNextActionUpdate(BaseModel):
    completed: Optional[bool] = None
    action_type: Optional[OtherActionType] = None
    label: Optional[str] = None


class OtherDbHealthSchema(BaseModel):
    lastIngest: str
    totalRecords: int


class OtherDigestPayload(BaseModel):
    generatedAt: str
    markdown: str
