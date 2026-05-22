"""Memory routes — list, search, and delete user memories."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Annotated

from dependencies import get_current_user, get_db, get_secrets
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import MemoryOut, UserOut
from app.infra.vault import VaultSecrets
from app.repositories import memory_repo

router = APIRouter(prefix="/memories", tags=["memories"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[UserOut, Depends(get_current_user)]
SecretsDep = Annotated[VaultSecrets, Depends(get_secrets)]


class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 3


@router.get("", response_model=list[MemoryOut])
async def list_memories(db: DbDep, user: CurrentUserDep) -> list[MemoryOut]:
    rows = await memory_repo.list_by_user(db, user.id)
    return [MemoryOut.model_validate(r) for r in rows]


@router.post("/search", response_model=list[MemoryOut])
async def search_memories(
    req: MemorySearchRequest,
    db: DbDep,
    user: CurrentUserDep,
    secrets: SecretsDep,
) -> list[MemoryOut]:
    from datetime import datetime

    from app.infra.openai_client import embed_one

    query_vec = await embed_one(req.query, secrets.openai_api_key)
    rows = await memory_repo.search_by_similarity(db, user.id, query_vec, top_k=req.top_k)
    now = datetime.now(UTC)
    return [
        MemoryOut(id=r["id"], summary=r["summary"], created_at=r.get("created_at", now))
        for r in rows
    ]


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: uuid.UUID,
    db: DbDep,
    user: CurrentUserDep,
) -> None:
    await memory_repo.delete(db, memory_id, user.id)
    await db.commit()
