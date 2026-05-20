"""Phase 4 — HTTP route tests for POST /chat."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

CONV_ID = str(uuid.uuid4())


@pytest.fixture
async def client(
    fake_secrets, fake_user, mock_db, mock_redis, mock_minio, mock_modelserver, mock_langfuse
):
    from dependencies import get_current_user, get_db
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
            get_current_user: lambda: fake_user,
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_chat_returns_200(client: AsyncClient) -> None:
    from api.app.domain.models import ChatResponse

    mock_response = ChatResponse(reply="Use a None check.", label="bug", sources=["gh://x/y#1"])

    with patch("app.services.chat_service.chat", new=AsyncMock(return_value=mock_response)):
        resp = await client.post(
            "/chat",
            json={"message": "null pointer crash", "conversation_id": CONV_ID},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Use a None check."
    assert body["label"] == "bug"
    assert "gh://x/y#1" in body["sources"]


async def test_chat_response_has_correct_schema(client: AsyncClient) -> None:
    from api.app.domain.models import ChatResponse

    mock_response = ChatResponse(reply="Answer here.", label="feature", sources=[])

    with patch("app.services.chat_service.chat", new=AsyncMock(return_value=mock_response)):
        resp = await client.post(
            "/chat",
            json={"message": "how to add feature?", "conversation_id": CONV_ID},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"reply", "label", "sources"}
    assert isinstance(body["sources"], list)


async def test_chat_requires_auth(
    fake_secrets, mock_db, mock_redis, mock_minio, mock_modelserver, mock_langfuse
) -> None:
    """POST /chat without a token must return 401."""
    from dependencies import get_current_user, get_db
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
            get_current_user: lambda: (_ for _ in ()).throw(
                AuthenticationError("Missing Authorization header")
            ),
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/chat",
            json={"message": "hello", "conversation_id": CONV_ID},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 401


async def test_chat_propagates_not_found(client: AsyncClient) -> None:
    from app.exceptions import NotFoundError

    with patch(
        "app.services.chat_service.chat",
        new=AsyncMock(side_effect=NotFoundError("Conversation not found")),
    ):
        resp = await client.post(
            "/chat",
            json={"message": "hello", "conversation_id": CONV_ID},
        )

    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


async def test_chat_propagates_permission_denied(client: AsyncClient) -> None:
    from app.exceptions import PermissionDenied

    with patch(
        "app.services.chat_service.chat",
        new=AsyncMock(side_effect=PermissionDenied("Not your conversation")),
    ):
        resp = await client.post(
            "/chat",
            json={"message": "hello", "conversation_id": CONV_ID},
        )

    assert resp.status_code == 403
    assert resp.json()["error"] == "permission_denied"
