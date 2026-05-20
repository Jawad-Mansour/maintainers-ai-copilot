"""Conversation repository — SQL only."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import Conversation


async def create(db: AsyncSession, user_id: uuid.UUID) -> Conversation:
    conv = Conversation(id=uuid.uuid4(), user_id=user_id)
    db.add(conv)
    return conv


async def get(db: AsyncSession, conv_id: uuid.UUID) -> Conversation | None:
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    return result.scalar_one_or_none()


async def list_by_user(db: AsyncSession, user_id: uuid.UUID) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_by_id(db: AsyncSession, conv_id: uuid.UUID) -> None:
    await db.execute(delete(Conversation).where(Conversation.id == conv_id))
