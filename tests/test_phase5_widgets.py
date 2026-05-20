"""Phase 5 — Widget CRUD route tests."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

WIDGET_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _fake_widget_out():
    from api.app.domain.models import WidgetOut

    return WidgetOut(
        id=WIDGET_ID,
        owner_id=OWNER_ID,
        name="Test Widget",
        allowed_origins=["http://localhost:3000"],
        theme={"color": "#1a1a1a"},
        greeting="How can I help?",
        enabled_tools=["classify_issue"],
        is_active=True,
        created_at=datetime(2024, 1, 1),
    )


@pytest.fixture
async def admin_client(fake_secrets, fake_admin, mock_db):
    from dependencies import get_current_user, get_db
    from main import app

    async def _db():
        yield mock_db

    app.state.secrets = fake_secrets
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_current_user: lambda: fake_admin,
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def user_client(fake_secrets, fake_user, mock_db):
    from dependencies import get_current_user, get_db
    from main import app

    async def _db():
        yield mock_db

    app.state.secrets = fake_secrets
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_current_user: lambda: fake_user,
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_create_widget_returns_201(admin_client: AsyncClient, fake_admin) -> None:
    widget = _fake_widget_out()

    with patch("app.services.widget_service.create_widget", new=AsyncMock(return_value=widget)):
        resp = await admin_client.post(
            "/widgets",
            json={
                "name": "Test Widget",
                "allowed_origins": ["http://localhost:3000"],
                "greeting": "How can I help?",
                "enabled_tools": ["classify_issue"],
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Widget"
    assert body["id"] == str(WIDGET_ID)


async def test_list_widgets_returns_200(admin_client: AsyncClient) -> None:
    with patch(
        "app.services.widget_service.list_widgets", new=AsyncMock(return_value=[_fake_widget_out()])
    ):
        resp = await admin_client.get("/widgets")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_widget_returns_200(admin_client: AsyncClient) -> None:
    with patch(
        "app.services.widget_service.get_widget", new=AsyncMock(return_value=_fake_widget_out())
    ):
        resp = await admin_client.get(f"/widgets/{WIDGET_ID}")

    assert resp.status_code == 200
    assert resp.json()["id"] == str(WIDGET_ID)


async def test_update_widget_returns_200(admin_client: AsyncClient) -> None:
    updated = _fake_widget_out()

    with patch("app.services.widget_service.update_widget", new=AsyncMock(return_value=updated)):
        resp = await admin_client.put(
            f"/widgets/{WIDGET_ID}",
            json={"name": "Updated Widget"},
        )

    assert resp.status_code == 200


async def test_delete_widget_returns_204(admin_client: AsyncClient) -> None:
    with patch("app.services.widget_service.delete_widget", new=AsyncMock(return_value=None)):
        resp = await admin_client.delete(f"/widgets/{WIDGET_ID}")

    assert resp.status_code == 204


async def test_create_widget_forbidden_for_non_admin(user_client: AsyncClient) -> None:
    resp = await user_client.post(
        "/widgets",
        json={"name": "Widget", "allowed_origins": []},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "permission_denied"


async def test_get_widget_not_found(admin_client: AsyncClient) -> None:
    from app.exceptions import NotFoundError

    with patch(
        "app.services.widget_service.get_widget",
        new=AsyncMock(side_effect=NotFoundError("Widget not found")),
    ):
        resp = await admin_client.get(f"/widgets/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


async def test_widget_js_returns_javascript(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(f"/widgets/widget.js?widget_id={WIDGET_ID}")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert str(WIDGET_ID) in resp.text
    assert "iframe" in resp.text
