"""Phase 2 — Tests for conversation/message service (unit, no HTTP)."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import NotFoundError, PermissionDenied

OWNER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
CONV_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _mock_conv(user_id: uuid.UUID = OWNER_ID) -> MagicMock:
    conv = MagicMock()
    conv.id = CONV_ID
    conv.user_id = user_id
    conv.created_at = datetime(2024, 1, 1)
    return conv


# ── create_conversation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_conversation_returns_out() -> None:
    from api.app.services.conversation_service import create_conversation

    db = AsyncMock()
    conv = _mock_conv()

    with patch("api.app.services.conversation_service.conversation_repo.create", return_value=conv):
        result = await create_conversation(db, OWNER_ID)

    assert result.id == CONV_ID
    assert result.user_id == OWNER_ID


# ── list_conversations ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_conversations_filters_by_user() -> None:
    from api.app.services.conversation_service import list_conversations

    db = AsyncMock()
    convs = [_mock_conv(), _mock_conv()]

    with patch(
        "api.app.services.conversation_service.conversation_repo.list_by_user",
        return_value=convs,
    ):
        result = await list_conversations(db, OWNER_ID)

    assert len(result) == 2


# ── delete_conversation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_raises_not_found_when_missing() -> None:
    from api.app.services.conversation_service import delete_conversation

    db = AsyncMock()
    with (
        patch(
            "api.app.services.conversation_service.conversation_repo.get",
            return_value=None,
        ),
        pytest.raises(NotFoundError),
    ):
        await delete_conversation(db, CONV_ID, OWNER_ID)


@pytest.mark.asyncio
async def test_delete_raises_permission_denied_for_wrong_user() -> None:
    from api.app.services.conversation_service import delete_conversation

    db = AsyncMock()
    conv = _mock_conv(user_id=OTHER_ID)

    with (
        patch("api.app.services.conversation_service.conversation_repo.get", return_value=conv),
        pytest.raises(PermissionDenied),
    ):
        await delete_conversation(db, CONV_ID, OWNER_ID)


@pytest.mark.asyncio
async def test_delete_succeeds_for_owner() -> None:
    from api.app.services.conversation_service import delete_conversation

    db = AsyncMock()
    conv = _mock_conv(user_id=OWNER_ID)

    with (
        patch("api.app.services.conversation_service.conversation_repo.get", return_value=conv),
        patch("api.app.services.conversation_service.conversation_repo.delete_by_id"),
        patch("api.app.services.conversation_service.audit_repo.log"),
    ):
        await delete_conversation(db, CONV_ID, OWNER_ID)
        db.commit.assert_called_once()


# ── add_message ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_message_raises_permission_denied_for_wrong_user() -> None:
    from api.app.domain.models import MessageCreate
    from api.app.services.conversation_service import add_message

    db = AsyncMock()
    conv = _mock_conv(user_id=OTHER_ID)

    with (
        patch("api.app.services.conversation_service.conversation_repo.get", return_value=conv),
        pytest.raises(PermissionDenied),
    ):
        await add_message(db, CONV_ID, OWNER_ID, MessageCreate(content="hi"))


@pytest.mark.asyncio
async def test_list_messages_raises_not_found_when_missing() -> None:
    from api.app.services.conversation_service import list_messages

    db = AsyncMock()
    with (
        patch(
            "api.app.services.conversation_service.conversation_repo.get",
            return_value=None,
        ),
        pytest.raises(NotFoundError),
    ):
        await list_messages(db, CONV_ID, OWNER_ID)
