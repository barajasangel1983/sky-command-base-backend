from datetime import datetime
from typing import Literal, Optional

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
