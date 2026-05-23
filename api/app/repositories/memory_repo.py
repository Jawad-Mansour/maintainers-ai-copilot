"""Memory repository — semantic search and write."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import Memory


async def search_by_similarity(
    db: AsyncSession,
    user_id: uuid.UUID,
    query_vec: list[float],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    vec_str = f"[{','.join(map(str, query_vec))}]"
    sql = text("""
        SELECT id, summary, created_at,
               1 - (embedding <=> CAST(:vec AS vector)) AS score
        FROM memories
        WHERE user_id = :user_id
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :top_k
    """)
    result = await db.execute(sql, {"vec": vec_str, "user_id": str(user_id), "top_k": top_k})
    return [dict(row._mapping) for row in result.fetchall()]


async def create(
    db: AsyncSession,
    user_id: uuid.UUID,
    summary: str,
    embedding: list[float],
) -> Memory:
    memory = Memory(user_id=user_id, summary=summary, embedding=embedding)
    db.add(memory)
    return memory


async def list_by_user(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[Memory]:
    from sqlalchemy import select

    result = await db.execute(
        select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at.desc())
    )
    return list(result.scalars().all())


async def delete(
    db: AsyncSession,
    memory_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    from sqlalchemy import delete as sa_delete

    await db.execute(sa_delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
