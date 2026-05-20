"""Phase 2 — HTTP route tests for auth and conversations."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(fake_secrets, fake_user, mock_db, mock_redis):
    from dependencies import get_current_user, get_db, get_redis, get_secrets
    from main import app

    async def _db():
        yield mock_db

    app.dependency_overrides.update(
        {
            get_db: _db,
            get_redis: lambda: mock_redis,
            get_secrets: lambda: fake_secrets,
            get_current_user: lambda: fake_user,
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Auth routes ───────────────────────────────────────────────────────────────


async def test_register_returns_201(client: AsyncClient) -> None:
    from api.app.domain.models import LoginResponse

    mock_response = LoginResponse(access_token="fake-jwt-token")

    with patch("app.services.auth_service.register", new=AsyncMock(return_value=mock_response)):
        resp = await client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "pass123"},
        )

    assert resp.status_code == 201
    assert resp.json()["access_token"] == "fake-jwt-token"


async def test_login_returns_200(client: AsyncClient) -> None:
    from api.app.domain.models import LoginResponse

    mock_response = LoginResponse(access_token="login-token")

    with patch("app.services.auth_service.login", new=AsyncMock(return_value=mock_response)):
        resp = await client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "pass"},
        )

    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"


async def test_me_returns_current_user(client: AsyncClient, fake_user) -> None:
    resp = await client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == fake_user.email


# ── Conversation routes ───────────────────────────────────────────────────────


async def test_create_conversation_returns_201(client: AsyncClient, fake_user) -> None:
    from api.app.domain.models import ConversationOut

    mock_conv = ConversationOut(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        user_id=fake_user.id,
        created_at=datetime(2024, 1, 1),
    )

    with patch(
        "app.services.conversation_service.create_conversation",
        new=AsyncMock(return_value=mock_conv),
    ):
        resp = await client.post("/conversations")

    assert resp.status_code == 201
    assert resp.json()["user_id"] == str(fake_user.id)


async def test_list_conversations_returns_200(client: AsyncClient) -> None:
    with patch(
        "app.services.conversation_service.list_conversations",
        new=AsyncMock(return_value=[]),
    ):
        resp = await client.get("/conversations")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_delete_conversation_returns_204(client: AsyncClient) -> None:
    conv_id = uuid.uuid4()

    with patch(
        "app.services.conversation_service.delete_conversation",
        new=AsyncMock(return_value=None),
    ):
        resp = await client.delete(f"/conversations/{conv_id}")

    assert resp.status_code == 204
