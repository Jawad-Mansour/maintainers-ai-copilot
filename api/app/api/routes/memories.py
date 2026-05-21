"""Memory routes — list and delete user memories."""

from __future__ import annotations

import uuid
from typing import Annotated

from dependencies import get_current_user, get_db
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import MemoryOut, UserOut
from app.repositories import memory_repo

router = APIRouter(prefix="/memories", tags=["memories"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[UserOut, Depends(get_current_user)]


@router.get("", response_model=list[MemoryOut])
async def list_memories(db: DbDep, user: CurrentUserDep) -> list[MemoryOut]:
    rows = await memory_repo.list_by_user(db, user.id)
    return [MemoryOut.model_validate(r) for r in rows]


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: uuid.UUID,
    db: DbDep,
    user: CurrentUserDep,
) -> None:
    await memory_repo.delete(db, memory_id, user.id)
    await db.commit()
