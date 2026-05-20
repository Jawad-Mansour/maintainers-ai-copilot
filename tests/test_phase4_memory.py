"""Phase 4 — Unit tests for memory service."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FAKE_VEC = [0.5] * 1536
USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.mark.asyncio
async def test_get_relevant_memories_returns_summaries() -> None:
    from api.app.services.memory_service import get_relevant_memories

    db = AsyncMock()
    rows = [{"summary": "User prefers concise answers"}, {"summary": "Bug in auth module"}]

    with (
        patch("api.app.services.memory_service.embed_one", return_value=FAKE_VEC),
        patch(
            "api.app.services.memory_service.memory_repo.search_by_similarity",
            return_value=rows,
        ),
    ):
        result = await get_relevant_memories(db, USER_ID, "auth issue", "sk-test")

    assert result == ["User prefers concise answers", "Bug in auth module"]


@pytest.mark.asyncio
async def test_get_relevant_memories_returns_empty_list_when_none() -> None:
    from api.app.services.memory_service import get_relevant_memories

    db = AsyncMock()

    with (
        patch("api.app.services.memory_service.embed_one", return_value=FAKE_VEC),
        patch("api.app.services.memory_service.memory_repo.search_by_similarity", return_value=[]),
    ):
        result = await get_relevant_memories(db, USER_ID, "question", "sk-test")

    assert result == []


@pytest.mark.asyncio
async def test_save_memory_embeds_and_calls_create() -> None:
    from api.app.services.memory_service import save_memory

    db = AsyncMock()
    mock_memory = MagicMock()
    mock_memory.id = uuid.uuid4()

    with (
        patch("api.app.services.memory_service.embed_one", return_value=FAKE_VEC) as mock_embed,
        patch(
            "api.app.services.memory_service.memory_repo.create",
            return_value=mock_memory,
        ) as mock_create,
        patch("api.app.services.memory_service.audit_repo.log"),
    ):
        await save_memory(db, USER_ID, "useful memory summary", "sk-test")

    mock_embed.assert_called_once_with("useful memory summary", "sk-test")
    mock_create.assert_called_once_with(db, USER_ID, "useful memory summary", FAKE_VEC)


@pytest.mark.asyncio
async def test_save_memory_writes_audit_log() -> None:
    """Every long-term write must produce an audit-log row (assignment requirement)."""
    from api.app.services.memory_service import save_memory

    db = AsyncMock()
    mock_memory = MagicMock()
    mock_memory.id = uuid.uuid4()

    with (
        patch("api.app.services.memory_service.embed_one", return_value=FAKE_VEC),
        patch("api.app.services.memory_service.memory_repo.create", return_value=mock_memory),
        patch("api.app.services.memory_service.audit_repo.log") as mock_audit,
    ):
        await save_memory(db, USER_ID, "summary", "sk-test")

    mock_audit.assert_called_once()
    call_kwargs = mock_audit.call_args.kwargs
    assert call_kwargs["action"] == "write_memory"
    assert call_kwargs["actor_id"] == USER_ID
    assert call_kwargs["target_id"] == mock_memory.id


@pytest.mark.asyncio
async def test_save_memory_does_not_commit(mock_db: AsyncMock) -> None:
    """save_memory must NOT commit — caller owns the transaction."""
    from api.app.services.memory_service import save_memory

    mock_memory = MagicMock()
    mock_memory.id = uuid.uuid4()

    with (
        patch("api.app.services.memory_service.embed_one", return_value=FAKE_VEC),
        patch("api.app.services.memory_service.memory_repo.create", return_value=mock_memory),
        patch("api.app.services.memory_service.audit_repo.log"),
    ):
        await save_memory(mock_db, USER_ID, "summary", "sk-test")

    mock_db.commit.assert_not_called()
