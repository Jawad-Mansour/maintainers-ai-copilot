"""Conversation + message service — business logic and transaction boundaries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ConversationOut, MessageCreate, MessageOut
from app.exceptions import NotFoundError, PermissionDenied
from app.repositories import audit_repo, conversation_repo, message_repo


async def create_conversation(db: AsyncSession, user_id: uuid.UUID) -> ConversationOut:
    conv = await conversation_repo.create(db, user_id)
    await db.commit()
    await db.refresh(conv)
    return ConversationOut.model_validate(conv)


async def list_conversations(db: AsyncSession, user_id: uuid.UUID) -> list[ConversationOut]:
    convs = await conversation_repo.list_by_user(db, user_id)
    return [ConversationOut.model_validate(c) for c in convs]


async def delete_conversation(db: AsyncSession, conv_id: uuid.UUID, user_id: uuid.UUID) -> None:
    conv = await conversation_repo.get(db, conv_id)
    if not conv:
        raise NotFoundError("Conversation not found")
    if conv.user_id != user_id:
        raise PermissionDenied()
    await audit_repo.log(db, actor_id=user_id, action="delete_conversation", target_id=conv_id)
    await conversation_repo.delete_by_id(db, conv_id)
    await db.commit()


async def add_message(
    db: AsyncSession,
    conv_id: uuid.UUID,
    user_id: uuid.UUID,
    req: MessageCreate,
) -> MessageOut:
    conv = await conversation_repo.get(db, conv_id)
    if not conv:
        raise NotFoundError("Conversation not found")
    if conv.user_id != user_id:
        raise PermissionDenied()
    msg = await message_repo.create(db, conv_id, "user", req.content)
    await db.commit()
    await db.refresh(msg)
    return MessageOut.model_validate(msg)


async def list_messages(
    db: AsyncSession, conv_id: uuid.UUID, user_id: uuid.UUID
) -> list[MessageOut]:
    conv = await conversation_repo.get(db, conv_id)
    if not conv:
        raise NotFoundError("Conversation not found")
    if conv.user_id != user_id:
        raise PermissionDenied()
    msgs = await message_repo.list_by_conversation(db, conv_id)
    return [MessageOut.model_validate(m) for m in msgs]
