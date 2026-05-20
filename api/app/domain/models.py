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
