"""Conversation and message routes.

Routes do HTTP only: parse request, call service, return response.
No SQLAlchemy or Redis here.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from dependencies import get_current_user, get_db
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ConversationOut, MessageCreate, MessageOut, UserOut
from app.services import conversation_service

router = APIRouter(prefix="/conversations", tags=["conversations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[UserOut, Depends(get_current_user)]


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(db: DbDep, user: CurrentUserDep) -> ConversationOut:
    return await conversation_service.create_conversation(db, user.id)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(db: DbDep, user: CurrentUserDep) -> list[ConversationOut]:
    return await conversation_service.list_conversations(db, user.id)


@router.delete("/{conv_id}", status_code=204)
async def delete_conversation(
    conv_id: uuid.UUID,
    db: DbDep,
    user: CurrentUserDep,
) -> None:
    await conversation_service.delete_conversation(db, conv_id, user.id)


@router.post("/{conv_id}/messages", response_model=MessageOut, status_code=201)
async def add_message(
    conv_id: uuid.UUID,
    req: MessageCreate,
    db: DbDep,
    user: CurrentUserDep,
) -> MessageOut:
    return await conversation_service.add_message(db, conv_id, user.id, req)


@router.get("/{conv_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conv_id: uuid.UUID,
    db: DbDep,
    user: CurrentUserDep,
) -> list[MessageOut]:
    return await conversation_service.list_messages(db, conv_id, user.id)
