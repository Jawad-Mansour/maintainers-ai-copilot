"""User repository — SQL only, no HTTP errors, no cache logic."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import User


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def count(db: AsyncSession) -> int:
    from sqlalchemy import func

    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar_one()


async def create(
    db: AsyncSession,
    email: str,
    hashed_password: str,
    role: str = "user",
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed_password,
        role=role,
    )
    db.add(user)
    return user
