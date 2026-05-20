"""Phase 2 — Tests for auth service and JWT handler (unit, no HTTP)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import AuthenticationError, ConflictError
from app.infra.jwt_handler import create_access_token, decode_access_token

SIGNING_KEY = "test-signing-key-for-jwt-tests-32ch"  # gitleaks:allow


# ── JWT handler unit tests ────────────────────────────────────────────────────


def test_create_and_decode_token() -> None:
    user_id = str(uuid.uuid4())
    token = create_access_token(user_id, "user", SIGNING_KEY)
    payload = decode_access_token(token, SIGNING_KEY)
    assert payload["sub"] == user_id
    assert payload["role"] == "user"


def test_decode_raises_on_wrong_key() -> None:
    token = create_access_token("user-id", "user", SIGNING_KEY)
    with pytest.raises(AuthenticationError, match="Invalid or expired token"):
        decode_access_token(token, "wrong-key-totally-different")


def test_decode_raises_on_garbage_token() -> None:
    with pytest.raises(AuthenticationError):
        decode_access_token("not.a.valid.jwt", SIGNING_KEY)


# ── Auth service unit tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_raises_conflict_if_email_taken() -> None:
    from api.app.domain.models import RegisterRequest
    from api.app.services.auth_service import register

    mock_db = AsyncMock()
    existing_user = MagicMock()

    with (
        patch("api.app.services.auth_service.user_repo.get_by_email", return_value=existing_user),
        pytest.raises(ConflictError, match="already registered"),
    ):
        await register(
            mock_db,
            RegisterRequest(email="dup@example.com", password="pass"),
            SIGNING_KEY,
        )


@pytest.mark.asyncio
async def test_register_creates_user_and_returns_token() -> None:
    from api.app.domain.models import RegisterRequest
    from api.app.services.auth_service import register

    mock_db = AsyncMock()
    new_user = MagicMock()
    new_user.id = uuid.uuid4()
    new_user.role = "user"

    with (
        patch("api.app.services.auth_service.user_repo.get_by_email", return_value=None),
        patch("api.app.services.auth_service.user_repo.create", return_value=new_user),
    ):
        result = await register(
            mock_db,
            RegisterRequest(email="new@example.com", password="pass123"),
            SIGNING_KEY,
        )

    assert result.access_token
    assert result.token_type == "bearer"


@pytest.mark.asyncio
async def test_login_raises_on_bad_password() -> None:
    from api.app.domain.models import LoginRequest
    from api.app.services.auth_service import login

    mock_db = AsyncMock()
    user = MagicMock()
    user.is_active = True

    with (
        patch("api.app.services.auth_service.user_repo.get_by_email", return_value=user),
        patch("api.app.services.auth_service._pwd") as mock_pwd,
    ):
        mock_pwd.verify.return_value = False
        with pytest.raises(AuthenticationError):
            await login(
                mock_db,
                LoginRequest(email="user@example.com", password="wrong_password"),
                SIGNING_KEY,
            )


@pytest.mark.asyncio
async def test_login_raises_when_user_not_found() -> None:
    from api.app.domain.models import LoginRequest
    from api.app.services.auth_service import login

    mock_db = AsyncMock()

    with (
        patch("api.app.services.auth_service.user_repo.get_by_email", return_value=None),
        pytest.raises(AuthenticationError),
    ):
        await login(
            mock_db,
            LoginRequest(email="ghost@example.com", password="pass"),
            SIGNING_KEY,
        )
