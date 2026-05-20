"""Pydantic domain models — distinct from SQLAlchemy ORM models in app/infra/db/models.py.

Routes and services use these. Repositories map ORM rows to these via model_validate().
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

# ── Output models (responses) ──────────────────────────────────────────────


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    created_at: datetime


# ── Request schemas (inputs) ───────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MessageCreate(BaseModel):
    content: str


# ── RAG schemas ────────────────────────────────────────────────────────────


class IngestRequest(BaseModel):
    text: str
    source: str
    label: str | None = None


class IngestResponse(BaseModel):
    chunks_stored: int


class SearchRequest(BaseModel):
    query: str
    conversation_id: UUID
    label: str | None = None
    source: str | None = None
    top_k: int = 5


class ChunkResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    text: str
    parent_text: str | None
    label: str | None
    source: str | None
    score: float


# ── Chat schemas ───────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    conversation_id: UUID


class ChatResponse(BaseModel):
    reply: str
    label: str
    sources: list[str]


# ── Widget schemas ─────────────────────────────────────────────────────────


class WidgetCreate(BaseModel):
    name: str
    allowed_origins: list[str] = []
    theme: dict | None = None
    greeting: str = "How can I help?"
    enabled_tools: list[str] = []


class WidgetUpdate(BaseModel):
    name: str | None = None
    allowed_origins: list[str] | None = None
    theme: dict | None = None
    greeting: str | None = None
    enabled_tools: list[str] | None = None
    is_active: bool | None = None


class WidgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: UUID
    name: str
    allowed_origins: list[str]
    theme: dict | None
    greeting: str
    enabled_tools: list[str]
    is_active: bool
    created_at: datetime
