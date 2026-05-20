"""Memory service — retrieve relevant past memories, persist new ones."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.openai_client import embed_one
from app.repositories import audit_repo, memory_repo


async def get_relevant_memories(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    api_key: str,
    top_k: int = 3,
) -> list[str]:
    """Return top-k memory summaries semantically relevant to query."""
    query_vec = await embed_one(query, api_key)
    rows = await memory_repo.search_by_similarity(db, user_id, query_vec, top_k=top_k)
    return [row["summary"] for row in rows]


async def save_memory(
    db: AsyncSession,
    user_id: uuid.UUID,
    summary: str,
    api_key: str,
) -> None:
    """Embed summary and add a memory row. Caller owns db.commit().

    Every long-term write produces an audit-log row per assignment requirement.
    """
    embedding = await embed_one(summary, api_key)
    memory = await memory_repo.create(db, user_id, summary, embedding)
    await audit_repo.log(
        db,
        actor_id=user_id,
        action="write_memory",
        target_id=memory.id,
    )
