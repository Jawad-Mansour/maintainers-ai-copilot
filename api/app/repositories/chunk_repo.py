"""Chunk repository — insert and hybrid-search text chunks."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String as SAString

from app.infra.db.models import Chunk
from app.services.chunker import ChunkRecord


async def bulk_insert(
    db: AsyncSession,
    records: list[ChunkRecord],
    embeddings: dict[uuid.UUID, list[float]],
) -> int:
    """Insert parent + child chunks. Returns count of child chunks stored."""
    for record in records:
        embedding = embeddings.get(record.id)
        db.add(
            Chunk(
                id=record.id,
                text=record.text,
                embedding=embedding,
                chunk_type=record.chunk_type,
                parent_id=record.parent_id,
                label=record.label,
                source=record.source,
            )
        )
    return sum(1 for r in records if r.chunk_type == "child")


async def hybrid_search(
    db: AsyncSession,
    query_vec: list[float],
    query_text: str,
    label: str | None = None,
    source: str | None = None,
    top_k: int = 20,
    final_k: int = 5,
) -> list[dict[str, Any]]:
    """0.6 × dense + 0.4 × sparse hybrid search with metadata filtering.

    Pulls top_k candidates by dense similarity, blends with BM25-style sparse
    score, returns final_k results ordered by combined score.
    """
    vec_str = f"[{','.join(map(str, query_vec))}]"

    sql = text("""
        WITH dense AS (
            SELECT id,
                   1 - (embedding <=> CAST(:query_vec AS vector)) AS dense_score
            FROM chunks
            WHERE chunk_type = 'child'
              AND (:label IS NULL OR label = :label)
              AND (:source IS NULL OR source = :source)
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:query_vec AS vector)
            LIMIT :top_k
        ),
        sparse AS (
            SELECT id,
                   ts_rank(
                       search_vector,
                       plainto_tsquery('english', :query_text)
                   ) AS sparse_score
            FROM chunks
            WHERE chunk_type = 'child'
              AND (:label IS NULL OR label = :label)
              AND (:source IS NULL OR source = :source)
              AND search_vector IS NOT NULL
              AND search_vector @@ plainto_tsquery('english', :query_text)
        ),
        combined AS (
            SELECT d.id,
                   0.6 * d.dense_score + 0.4 * COALESCE(s.sparse_score, 0.0) AS score
            FROM dense d
            LEFT JOIN sparse s ON d.id = s.id
        )
        SELECT c.id, c.text, c.parent_id, c.label, c.source, comb.score
        FROM combined comb
        JOIN chunks c ON c.id = comb.id
        ORDER BY comb.score DESC
        LIMIT :final_k
    """).bindparams(
        bindparam("label", type_=SAString()),
        bindparam("source", type_=SAString()),
        bindparam("query_text", type_=SAString()),
        bindparam("query_vec", type_=SAString()),
    )

    result = await db.execute(
        sql,
        {
            "query_vec": vec_str,
            "query_text": query_text,
            "label": label,
            "source": source,
            "top_k": top_k,
            "final_k": final_k,
        },
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def get_parent_text(db: AsyncSession, parent_id: uuid.UUID) -> str | None:
    result = await db.execute(
        text("SELECT text FROM chunks WHERE id = :id"),
        {"id": str(parent_id)},
    )
    row = result.fetchone()
    return row[0] if row else None
