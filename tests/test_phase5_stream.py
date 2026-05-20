"""Phase 5 — SSE streaming endpoint tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

CONV_ID = str(uuid.uuid4())
WIDGET_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


async def _sse_generator(*events: str):
    """Async generator that yields pre-built SSE event strings."""
    for e in events:
        yield e


@pytest.fixture
async def client(
    fake_secrets, fake_user, mock_db, mock_redis, mock_minio, mock_modelserver, mock_langfuse
):
    from dependencies import get_current_user, get_db, get_redis
    from main import app

    async def _db():
        yield mock_db

    app.state.secrets = fake_secrets
    app.state.redis_client = mock_redis
    app.state.minio_client = mock_minio
    app.state.modelserver_client = mock_modelserver
    app.state.langfuse = mock_langfuse
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_redis: lambda: mock_redis,
            get_current_user: lambda: fake_user,
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_stream_returns_event_stream_content_type(client: AsyncClient) -> None:
    gen = _sse_generator(
        'data: {"type": "token", "content": "Hello"}\n\n',
        'data: {"type": "done", "label": "bug", "sources": []}\n\n',
    )

    with patch("app.services.chat_service.stream_chat", new=AsyncMock(return_value=gen)):
        resp = await client.post(
            "/chat/stream",
            json={"message": "fix crash", "conversation_id": CONV_ID},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


async def test_stream_response_contains_token_events(client: AsyncClient) -> None:
    token_event = 'data: {"type": "token", "content": "Fix"}\n\n'
    done_event = 'data: {"type": "done", "label": "unknown", "sources": []}\n\n'
    gen = _sse_generator(token_event, done_event)

    with patch("app.services.chat_service.stream_chat", new=AsyncMock(return_value=gen)):
        resp = await client.post(
            "/chat/stream",
            json={"message": "help me", "conversation_id": CONV_ID},
        )

    assert "token" in resp.text
    assert "done" in resp.text


async def test_stream_requires_auth(
    fake_secrets, mock_db, mock_redis, mock_minio, mock_modelserver, mock_langfuse
) -> None:
    from dependencies import get_current_user, get_db, get_redis
    from main import app

    from app.exceptions import AuthenticationError

    async def _db():
        yield mock_db

    app.state.secrets = fake_secrets
    app.state.redis_client = mock_redis
    app.state.minio_client = mock_minio
    app.state.modelserver_client = mock_modelserver
    app.state.langfuse = mock_langfuse
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_redis: lambda: mock_redis,
            get_current_user: lambda: (_ for _ in ()).throw(
                AuthenticationError("Missing Authorization header")
            ),
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/chat/stream",
            json={"message": "hello", "conversation_id": CONV_ID},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 401


async def test_stream_cors_blocked_by_widget_allowed_origins(
    fake_secrets, fake_user, mock_db, mock_redis, mock_minio, mock_modelserver, mock_langfuse
) -> None:
    """Request from an origin not in widget.allowed_origins must get 403."""
    from dependencies import get_current_user, get_db, get_redis
    from main import app

    async def _db():
        yield mock_db

    # Widget that only allows http://allowed.com
    fake_widget = MagicMock()
    fake_widget.allowed_origins = ["http://allowed.com"]

    app.state.secrets = fake_secrets
    app.state.redis_client = mock_redis
    app.state.minio_client = mock_minio
    app.state.modelserver_client = mock_modelserver
    app.state.langfuse = mock_langfuse
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_redis: lambda: mock_redis,
            get_current_user: lambda: fake_user,
        }
    )

    with patch("app.repositories.widget_repo.get", new=AsyncMock(return_value=fake_widget)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/chat/stream?widget_id={WIDGET_ID}",
                json={"message": "hello", "conversation_id": CONV_ID},
                headers={"origin": "http://evil.com"},
            )

    app.dependency_overrides.clear()
    assert resp.status_code == 403
    assert resp.json()["error"] == "permission_denied"


async def test_stream_allowed_origin_passes(
    fake_secrets, fake_user, mock_db, mock_redis, mock_minio, mock_modelserver, mock_langfuse
) -> None:
    """Request from an allowed origin must proceed (not 403)."""
    from dependencies import get_current_user, get_db, get_redis
    from main import app

    async def _db():
        yield mock_db

    fake_widget = MagicMock()
    fake_widget.allowed_origins = ["http://allowed.com"]

    gen = _sse_generator('data: {"type": "done", "label": "unknown", "sources": []}\n\n')

    app.state.secrets = fake_secrets
    app.state.redis_client = mock_redis
    app.state.minio_client = mock_minio
    app.state.modelserver_client = mock_modelserver
    app.state.langfuse = mock_langfuse
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_redis: lambda: mock_redis,
            get_current_user: lambda: fake_user,
        }
    )

    with (
        patch("app.repositories.widget_repo.get", new=AsyncMock(return_value=fake_widget)),
        patch("app.services.chat_service.stream_chat", new=AsyncMock(return_value=gen)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/chat/stream?widget_id={WIDGET_ID}",
                json={"message": "hello", "conversation_id": CONV_ID},
                headers={"origin": "http://allowed.com"},
            )

    app.dependency_overrides.clear()
    assert resp.status_code == 200
