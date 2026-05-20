"""Phase 4 — Unit tests for chat service (bugs and ownership checks)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models import ChatRequest
from app.exceptions import NotFoundError, PermissionDenied

OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
CONV_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
FAKE_VEC = [0.1] * 1536


def _mock_conv(user_id: uuid.UUID = OWNER_ID) -> MagicMock:
    conv = MagicMock()
    conv.id = CONV_ID
    conv.user_id = user_id
    conv.created_at = datetime(2024, 1, 1)
    return conv


def _make_chat_req() -> ChatRequest:
    return ChatRequest(message="fix the null pointer", conversation_id=CONV_ID)


def _mock_modelserver() -> AsyncMock:
    ms = AsyncMock()
    ms.classify.return_value = ["bug"]
    ms.rerank.return_value = [0.9, 0.8, 0.7]
    return ms


def _mock_langfuse() -> MagicMock:
    lf = MagicMock()
    trace = MagicMock()
    gen = MagicMock()
    lf.trace.return_value = trace
    trace.generation.return_value = gen
    return lf


def _mock_openai_response(text: str = "Use None check before calling method.") -> MagicMock:
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── Ownership checks ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_raises_not_found_for_missing_conversation() -> None:
    from api.app.services.chat_service import chat

    db = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = None

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=None),
        pytest.raises(NotFoundError, match="Conversation not found"),
    ):
        await chat(
            db=db,
            req=_make_chat_req(),
            user_id=OWNER_ID,
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=_mock_modelserver(),
            langfuse=_mock_langfuse(),
            redis=redis,
        )


@pytest.mark.asyncio
async def test_chat_raises_permission_denied_for_wrong_user() -> None:
    from api.app.services.chat_service import chat

    db = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = None
    conv = _mock_conv(user_id=OTHER_ID)

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=conv),
        pytest.raises(PermissionDenied, match="Not your conversation"),
    ):
        await chat(
            db=db,
            req=_make_chat_req(),
            user_id=OWNER_ID,
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=_mock_modelserver(),
            langfuse=_mock_langfuse(),
            redis=redis,
        )


# ── No auto-memory save (D14) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_does_not_auto_save_memory() -> None:
    """D14: memory must NOT be auto-saved on every turn."""
    from api.app.services.chat_service import chat

    db = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = None
    conv = _mock_conv()

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=conv),
        patch("api.app.services.chat_service.modelserver_client", create=True),
        patch(
            "api.app.services.chat_service.memory_service.get_relevant_memories",
            return_value=[],
        ),
        patch("api.app.services.chat_service.rag_search", return_value=[]),
        patch(
            "api.app.services.chat_service.message_repo.list_by_conversation",
            return_value=[],
        ),
        patch("api.app.services.chat_service.message_repo.create"),
        patch("api.app.services.memory_service.save_memory") as mock_save,
        patch(
            "api.app.services.chat_service.AsyncOpenAI",
            return_value=MagicMock(
                chat=MagicMock(
                    completions=MagicMock(create=AsyncMock(return_value=_mock_openai_response()))
                )
            ),
        ),
        patch(
            "api.app.services.chat_service.load_prompt",
            return_value="system {label} {memories} {chunks}",
        ),
    ):
        await chat(
            db=db,
            req=_make_chat_req(),
            user_id=OWNER_ID,
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=_mock_modelserver(),
            langfuse=_mock_langfuse(),
            redis=redis,
        )

    mock_save.assert_not_called()


# ── Redis history ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_serves_history_from_redis_cache() -> None:
    from api.app.services.chat_service import chat

    db = AsyncMock()
    redis = AsyncMock()
    cached = [{"role": "user", "content": "previous question"}]
    redis.get.return_value = json.dumps(cached)
    conv = _mock_conv()

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=conv),
        patch(
            "api.app.services.chat_service.memory_service.get_relevant_memories",
            return_value=[],
        ),
        patch("api.app.services.chat_service.rag_search", return_value=[]),
        patch("api.app.services.chat_service.message_repo.create"),
        patch("api.app.services.chat_service.message_repo.list_by_conversation") as mock_list,
        patch(
            "api.app.services.chat_service.AsyncOpenAI",
            return_value=MagicMock(
                chat=MagicMock(
                    completions=MagicMock(create=AsyncMock(return_value=_mock_openai_response()))
                )
            ),
        ),
        patch(
            "api.app.services.chat_service.load_prompt",
            return_value="{label} {memories} {chunks}",
        ),
    ):
        await chat(
            db=db,
            req=_make_chat_req(),
            user_id=OWNER_ID,
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=_mock_modelserver(),
            langfuse=_mock_langfuse(),
            redis=redis,
        )

    # Redis cache hit → DB history query must NOT be called
    mock_list.assert_not_called()


@pytest.mark.asyncio
async def test_chat_updates_redis_after_reply() -> None:
    from api.app.services.chat_service import chat

    db = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = None
    conv = _mock_conv()

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=conv),
        patch(
            "api.app.services.chat_service.memory_service.get_relevant_memories",
            return_value=[],
        ),
        patch("api.app.services.chat_service.rag_search", return_value=[]),
        patch(
            "api.app.services.chat_service.message_repo.list_by_conversation",
            return_value=[],
        ),
        patch("api.app.services.chat_service.message_repo.create"),
        patch(
            "api.app.services.chat_service.AsyncOpenAI",
            return_value=MagicMock(
                chat=MagicMock(
                    completions=MagicMock(
                        create=AsyncMock(return_value=_mock_openai_response("The answer."))
                    )
                )
            ),
        ),
        patch(
            "api.app.services.chat_service.load_prompt",
            return_value="{label} {memories} {chunks}",
        ),
    ):
        await chat(
            db=db,
            req=_make_chat_req(),
            user_id=OWNER_ID,
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=_mock_modelserver(),
            langfuse=_mock_langfuse(),
            redis=redis,
        )

    redis.set.assert_called_once()
    set_args = redis.set.call_args
    stored = json.loads(set_args[0][1])
    assert stored[-1]["role"] == "assistant"
    assert stored[-1]["content"] == "The answer."
    assert stored[-2]["role"] == "user"


# ── Response shape ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_returns_correct_response_shape() -> None:
    from api.app.domain.models import ChunkResult
    from api.app.services.chat_service import chat

    db = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = None
    conv = _mock_conv()

    chunk = ChunkResult(
        id=uuid.uuid4(),
        text="relevant chunk",
        parent_text=None,
        label="bug",
        source="gh://x/y#1",
        score=0.9,
    )

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=conv),
        patch(
            "api.app.services.chat_service.memory_service.get_relevant_memories",
            return_value=[],
        ),
        patch("api.app.services.chat_service.rag_search", return_value=[chunk]),
        patch(
            "api.app.services.chat_service.message_repo.list_by_conversation",
            return_value=[],
        ),
        patch("api.app.services.chat_service.message_repo.create"),
        patch(
            "api.app.services.chat_service.AsyncOpenAI",
            return_value=MagicMock(
                chat=MagicMock(
                    completions=MagicMock(
                        create=AsyncMock(return_value=_mock_openai_response("Fix it."))
                    )
                )
            ),
        ),
        patch(
            "api.app.services.chat_service.load_prompt",
            return_value="{label} {memories} {chunks}",
        ),
    ):
        result = await chat(
            db=db,
            req=_make_chat_req(),
            user_id=OWNER_ID,
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=_mock_modelserver(),
            langfuse=_mock_langfuse(),
            redis=redis,
        )

    assert result.reply == "Fix it."
    assert result.label == "bug"
    assert "gh://x/y#1" in result.sources
