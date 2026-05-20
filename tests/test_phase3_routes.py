"""Phase 3 — HTTP route tests for RAG ingest and search."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

CONV_ID = str(uuid.uuid4())


@pytest.fixture
async def client(
    fake_secrets, fake_user, fake_admin, mock_db, mock_redis, mock_minio, mock_modelserver
):
    from dependencies import get_current_user, get_db, require_admin
    from main import app

    async def _db():
        yield mock_db

    app.state.secrets = fake_secrets
    app.state.redis_client = mock_redis
    app.state.minio_client = mock_minio
    app.state.modelserver_client = mock_modelserver
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_current_user: lambda: fake_user,
            require_admin: lambda: fake_admin,
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def admin_client(fake_secrets, fake_admin, mock_db, mock_redis, mock_minio, mock_modelserver):
    from dependencies import get_current_user, get_db, require_admin
    from main import app

    async def _db():
        yield mock_db

    app.state.secrets = fake_secrets
    app.state.redis_client = mock_redis
    app.state.minio_client = mock_minio
    app.state.modelserver_client = mock_modelserver
    app.dependency_overrides.update(
        {
            get_db: _db,
            get_current_user: lambda: fake_admin,
            require_admin: lambda: fake_admin,
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_ingest_returns_201(admin_client: AsyncClient) -> None:
    with patch("app.services.rag_service.ingest", new=AsyncMock(return_value=None)) as mock_ingest:
        from api.app.domain.models import IngestResponse

        mock_ingest.return_value = IngestResponse(chunks_stored=5)
        resp = await admin_client.post(
            "/rag/ingest",
            json={"text": "some issue text", "source": "gh://x/y#1", "label": "bug"},
        )

    assert resp.status_code == 201
    assert resp.json()["chunks_stored"] == 5


async def test_ingest_requires_admin(client: AsyncClient) -> None:
    """Ingest endpoint requires admin role — regular user gets 403."""
    from dependencies import require_admin
    from main import app

    from app.exceptions import PermissionDenied

    app.dependency_overrides[require_admin] = lambda: (_ for _ in ()).throw(
        PermissionDenied("Admin access required")
    )

    resp = await client.post(
        "/rag/ingest",
        json={"text": "text", "source": "gh://x/y#1"},
    )

    assert resp.status_code == 403


async def test_search_returns_200(client: AsyncClient) -> None:
    from api.app.domain.models import ChunkResult

    chunk = ChunkResult(
        id=uuid.uuid4(),
        text="relevant text",
        parent_text=None,
        label="bug",
        source="gh://x/y#1",
        score=0.9,
    )

    with patch(
        "app.services.rag_service.search",
        new=AsyncMock(return_value=[chunk]),
    ):
        resp = await client.post(
            "/rag/search",
            json={
                "query": "null pointer",
                "conversation_id": CONV_ID,
                "top_k": 5,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["source"] == "gh://x/y#1"


async def test_search_requires_auth(
    mock_db, mock_redis, fake_secrets, mock_minio, mock_modelserver
) -> None:
    from dependencies import get_current_user, get_db
    from main import app

    from app.exceptions import AuthenticationError

    async def _db():
        yield mock_db

    app.state.secrets = fake_secrets
    app.state.redis_client = mock_redis
    app.state.minio_client = mock_minio
    app.state.modelserver_client = mock_modelserver
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
            "/rag/search",
            json={"query": "test", "conversation_id": CONV_ID},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 401
