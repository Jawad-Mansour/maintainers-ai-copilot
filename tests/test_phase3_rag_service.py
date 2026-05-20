"""Phase 3 — Unit tests for RAG service (ingest + search)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

CONV_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
FAKE_VEC = [0.1] * 1536


def _make_row(i: int) -> dict:
    return {
        "id": uuid.uuid4(),
        "text": f"chunk text {i}",
        "parent_id": None,
        "label": "bug",
        "source": "gh://x/y#1",
        "score": float(i) / 10,
    }


@pytest.mark.asyncio
async def test_ingest_stores_correct_count() -> None:
    from api.app.domain.models import IngestRequest
    from api.app.services.rag_service import ingest

    db = AsyncMock()
    req = IngestRequest(text="fix the null pointer bug in auth", source="gh://x/y#1", label="bug")

    with (
        patch(
            "api.app.services.rag_service.embed_texts",
            side_effect=lambda texts, key: [FAKE_VEC] * len(texts),
        ),
        patch("api.app.services.rag_service.chunk_repo.bulk_insert", return_value=3),
    ):
        result = await ingest(db, req, "sk-test")

    assert result.chunks_stored == 3
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_search_calls_reranker() -> None:
    from api.app.domain.models import SearchRequest
    from api.app.services.rag_service import search

    db = AsyncMock()
    minio = MagicMock()
    modelserver = AsyncMock()
    modelserver.rerank.return_value = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]

    rows = [_make_row(i) for i in range(7)]
    req = SearchRequest(query="null pointer bug", conversation_id=CONV_ID, top_k=5)

    with (
        patch("api.app.services.rag_service.embed_one", return_value=FAKE_VEC),
        patch("api.app.services.rag_service._hypothetical_answer", return_value="answer"),
        patch("api.app.services.rag_service.chunk_repo.hybrid_search", return_value=rows),
        patch("api.app.services.rag_service.chunk_repo.get_parent_text", return_value=None),
        patch("api.app.services.rag_service.save_chunk_snapshot"),
    ):
        results = await search(db, req, "sk-test", minio, modelserver)

    modelserver.rerank.assert_called_once()
    assert len(results) == 5


@pytest.mark.asyncio
async def test_search_rerank_sorts_by_score() -> None:
    from api.app.domain.models import SearchRequest
    from api.app.services.rag_service import search

    db = AsyncMock()
    minio = MagicMock()
    modelserver = AsyncMock()
    # rerank gives highest score to last row
    scores = [0.1, 0.2, 0.9, 0.3, 0.4]
    modelserver.rerank.return_value = scores

    rows = [_make_row(i) for i in range(5)]
    req = SearchRequest(query="test query", conversation_id=CONV_ID, top_k=3)

    with (
        patch("api.app.services.rag_service.embed_one", return_value=FAKE_VEC),
        patch("api.app.services.rag_service._hypothetical_answer", return_value="hyp"),
        patch("api.app.services.rag_service.chunk_repo.hybrid_search", return_value=rows),
        patch("api.app.services.rag_service.chunk_repo.get_parent_text", return_value=None),
        patch("api.app.services.rag_service.save_chunk_snapshot"),
    ):
        results = await search(db, req, "sk-test", minio, modelserver)

    # highest rerank score (0.9 at index 2) should be first
    assert results[0].text == rows[2]["text"]
    assert len(results) == 3


@pytest.mark.asyncio
async def test_search_hybrid_uses_20_candidates() -> None:
    """hybrid_search must be called with final_k=20 so reranker gets full candidate pool."""
    from api.app.domain.models import SearchRequest
    from api.app.services.rag_service import search

    db = AsyncMock()
    minio = MagicMock()
    modelserver = AsyncMock()
    modelserver.rerank.return_value = []

    req = SearchRequest(query="q", conversation_id=CONV_ID, top_k=5)

    with (
        patch("api.app.services.rag_service.embed_one", return_value=FAKE_VEC),
        patch("api.app.services.rag_service._hypothetical_answer", return_value="h"),
        patch("api.app.services.rag_service.chunk_repo.hybrid_search", return_value=[]) as mock_hs,
        patch("api.app.services.rag_service.save_chunk_snapshot"),
    ):
        await search(db, req, "sk-test", minio, modelserver)

    assert mock_hs.call_args.kwargs["final_k"] == 20


@pytest.mark.asyncio
async def test_search_empty_results_skips_rerank() -> None:
    from api.app.domain.models import SearchRequest
    from api.app.services.rag_service import search

    db = AsyncMock()
    minio = MagicMock()
    modelserver = AsyncMock()

    req = SearchRequest(query="q", conversation_id=CONV_ID, top_k=5)

    with (
        patch("api.app.services.rag_service.embed_one", return_value=FAKE_VEC),
        patch("api.app.services.rag_service._hypothetical_answer", return_value="h"),
        patch("api.app.services.rag_service.chunk_repo.hybrid_search", return_value=[]),
        patch("api.app.services.rag_service.save_chunk_snapshot"),
    ):
        results = await search(db, req, "sk-test", minio, modelserver)

    modelserver.rerank.assert_not_called()
    assert results == []
