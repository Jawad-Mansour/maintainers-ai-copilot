"""Phase 5 — Unit tests for tool definitions and executor."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Tool definition structure ─────────────────────────────────────────────────


def test_all_tools_have_required_fields() -> None:
    from api.app.tools.definitions import ALL_TOOLS

    assert len(ALL_TOOLS) == 5
    for tool in ALL_TOOLS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"


def test_tool_names_are_unique() -> None:
    from api.app.tools.definitions import ALL_TOOLS

    names = [t["function"]["name"] for t in ALL_TOOLS]
    assert len(names) == len(set(names))


def test_expected_tool_names_present() -> None:
    from api.app.tools.definitions import ALL_TOOLS

    names = {t["function"]["name"] for t in ALL_TOOLS}
    assert names == {
        "classify_issue",
        "search_knowledge_base",
        "extract_entities",
        "summarize_thread",
        "write_memory",
    }


def test_classify_issue_requires_text() -> None:
    from api.app.tools.definitions import CLASSIFY_ISSUE

    params = CLASSIFY_ISSUE["function"]["parameters"]
    assert "text" in params["properties"]
    assert "text" in params["required"]


def test_search_knowledge_base_requires_query() -> None:
    from api.app.tools.definitions import SEARCH_KNOWLEDGE_BASE

    params = SEARCH_KNOWLEDGE_BASE["function"]["parameters"]
    assert "query" in params["properties"]
    assert "query" in params["required"]


def test_write_memory_requires_summary() -> None:
    from api.app.tools.definitions import WRITE_MEMORY

    params = WRITE_MEMORY["function"]["parameters"]
    assert "summary" in params["properties"]
    assert "summary" in params["required"]


# ── Tool executor ─────────────────────────────────────────────────────────────


def _make_ctx(**overrides) -> object:
    from api.app.tools.executor import ToolContext

    defaults = dict(
        db=AsyncMock(),
        user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        conversation_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        api_key="sk-test",
        minio_client=MagicMock(),
        modelserver_client=AsyncMock(),
        history=[],
    )
    defaults.update(overrides)
    return ToolContext(**defaults)


@pytest.mark.asyncio
async def test_execute_classify_issue() -> None:
    from api.app.tools.executor import execute_tool

    ctx = _make_ctx()
    ctx.modelserver_client.classify = AsyncMock(return_value=["bug"])

    result = await execute_tool("classify_issue", {"text": "null pointer on DataFrame"}, ctx)
    assert result == "bug"
    ctx.modelserver_client.classify.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_classify_returns_unknown_on_empty() -> None:
    from api.app.tools.executor import execute_tool

    ctx = _make_ctx()
    ctx.modelserver_client.classify = AsyncMock(return_value=[])

    result = await execute_tool("classify_issue", {"text": "some text"}, ctx)
    assert result == "unknown"


@pytest.mark.asyncio
async def test_execute_extract_entities() -> None:
    from api.app.tools.executor import execute_tool

    ctx = _make_ctx()
    ctx.modelserver_client.ner = AsyncMock(return_value=[{"text": "pandas", "label": "PACKAGE"}])

    result = await execute_tool("extract_entities", {"text": "pandas raises ValueError"}, ctx)
    data = json.loads(result)
    assert data[0]["label"] == "PACKAGE"


@pytest.mark.asyncio
async def test_execute_write_memory() -> None:
    from api.app.tools.executor import execute_tool

    ctx = _make_ctx()

    with patch("app.services.memory_service.save_memory", new=AsyncMock()):
        result = await execute_tool(
            "write_memory", {"summary": "User prefers concise replies."}, ctx
        )

    assert "Memory saved" in result


@pytest.mark.asyncio
async def test_execute_unknown_tool_raises() -> None:
    from api.app.tools.executor import execute_tool
    from app.exceptions import ToolFailure

    ctx = _make_ctx()
    with pytest.raises(ToolFailure, match="Unknown tool"):
        await execute_tool("does_not_exist", {}, ctx)


# ── Tool-calling loop in chat_service ────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_tool_loop_no_tools_called() -> None:
    """LLM returns final answer on first call — no tool calls."""
    from api.app.domain.models import ChatRequest
    from api.app.services.chat_service import chat

    conv = MagicMock()
    conv.user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    choice = MagicMock()
    choice.message.content = "Here is the answer."
    choice.message.tool_calls = None
    resp = MagicMock()
    resp.choices = [choice]

    db = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = None

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=conv),
        patch(
            "api.app.services.chat_service.memory_service.get_relevant_memories", return_value=[]
        ),
        patch("api.app.services.chat_service.message_repo.list_by_conversation", return_value=[]),
        patch("api.app.services.chat_service.message_repo.create"),
        patch(
            "api.app.services.chat_service.AsyncOpenAI",
            return_value=MagicMock(
                chat=MagicMock(completions=MagicMock(create=AsyncMock(return_value=resp)))
            ),
        ),
        patch("api.app.services.chat_service.load_prompt", return_value="{memories}"),
    ):
        result = await chat(
            db=db,
            req=ChatRequest(
                message="How do I fix this?",
                conversation_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            ),
            user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=AsyncMock(),
            langfuse=MagicMock(),
            redis=redis,
        )

    assert result.reply == "Here is the answer."
    assert result.label == "unknown"


@pytest.mark.asyncio
async def test_chat_tool_loop_classify_sets_label() -> None:
    """LLM calls classify_issue first, then gives final answer."""
    from api.app.domain.models import ChatRequest
    from api.app.services.chat_service import chat

    conv = MagicMock()
    conv.user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # First LLM call returns a tool_call for classify_issue
    tc = MagicMock()
    tc.id = "call_001"
    tc.function.name = "classify_issue"
    tc.function.arguments = json.dumps({"text": "crash on merge"})

    first_choice = MagicMock()
    first_choice.message.content = None
    first_choice.message.tool_calls = [tc]
    first_resp = MagicMock()
    first_resp.choices = [first_choice]

    # Second LLM call returns the final answer (no tool calls)
    second_choice = MagicMock()
    second_choice.message.content = "This is a bug."
    second_choice.message.tool_calls = None
    second_resp = MagicMock()
    second_resp.choices = [second_choice]

    db = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = None
    ms = AsyncMock()
    ms.classify.return_value = ["bug"]

    with (
        patch("api.app.services.chat_service.conversation_repo.get", return_value=conv),
        patch(
            "api.app.services.chat_service.memory_service.get_relevant_memories", return_value=[]
        ),
        patch("api.app.services.chat_service.message_repo.list_by_conversation", return_value=[]),
        patch("api.app.services.chat_service.message_repo.create"),
        patch(
            "api.app.services.chat_service.AsyncOpenAI",
            return_value=MagicMock(
                chat=MagicMock(
                    completions=MagicMock(create=AsyncMock(side_effect=[first_resp, second_resp]))
                )
            ),
        ),
        patch("api.app.services.chat_service.load_prompt", return_value="{memories}"),
    ):
        result = await chat(
            db=db,
            req=ChatRequest(
                message="crash on merge",
                conversation_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            ),
            user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            api_key="sk-test",
            minio_client=MagicMock(),
            modelserver_client=ms,
            langfuse=MagicMock(),
            redis=redis,
        )

    assert result.reply == "This is a bug."
    assert result.label == "bug"
