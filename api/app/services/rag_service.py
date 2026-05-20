"""RAG service — ingest documents, HyDE search, rerank, MinIO snapshot."""

from __future__ import annotations

from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ChunkResult, IngestRequest, IngestResponse, SearchRequest
from app.infra.minio_client import save_chunk_snapshot
from app.infra.observability import get_logger
from app.infra.openai_client import embed_one, embed_texts
from app.repositories import chunk_repo
from app.services.chunker import make_chunks

logger = get_logger(__name__)

_HYDE_PROMPT = (
    "Write a short GitHub issue comment that would directly answer this question:\n"
    "{query}\n\nAnswer:"
)


async def ingest(db: AsyncSession, req: IngestRequest, api_key: str) -> IngestResponse:
    records = make_chunks(req.text, source=req.source, label=req.label)
    child_records = [r for r in records if r.chunk_type == "child"]

    embeddings_list = await embed_texts([r.text for r in child_records], api_key)
    embeddings = {r.id: emb for r, emb in zip(child_records, embeddings_list, strict=True)}

    stored = await chunk_repo.bulk_insert(db, records, embeddings)
    await db.commit()
    logger.info("ingest_complete", source=req.source, chunks=stored)
    return IngestResponse(chunks_stored=stored)


async def search(
    db: AsyncSession,
    req: SearchRequest,
    api_key: str,
    minio_client: Minio,
) -> list[ChunkResult]:
    # HyDE: blend original query embedding 50/50 with hypothetical answer embedding
    query_vec = await embed_one(req.query, api_key)
    hyp_text = await _hypothetical_answer(req.query, api_key)
    hyp_vec = await embed_one(hyp_text, api_key)
    combined_vec = [(q + h) / 2.0 for q, h in zip(query_vec, hyp_vec, strict=True)]

    rows = await chunk_repo.hybrid_search(
        db,
        query_vec=combined_vec,
        query_text=req.query,
        label=req.label,
        source=req.source,
        top_k=20,
        final_k=req.top_k,
    )

    results: list[ChunkResult] = []
    for row in rows:
        parent_text = None
        if row["parent_id"]:
            parent_text = await chunk_repo.get_parent_text(db, row["parent_id"])
        results.append(
            ChunkResult(
                id=row["id"],
                text=row["text"],
                parent_text=parent_text,
                label=row["label"],
                source=row["source"],
                score=float(row["score"]),
            )
        )

    await save_chunk_snapshot(
        minio_client,
        str(req.conversation_id),
        [{"id": str(r.id), "text": r.text, "score": r.score, "source": r.source} for r in results],
    )

    return results


async def _hypothetical_answer(query: str, api_key: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": _HYDE_PROMPT.format(query=query)}],
            max_tokens=150,
            temperature=0.7,
        )
        return resp.choices[0].message.content or query
    except Exception:
        logger.warning("hyde_generation_failed", query=query[:80])
        return query
