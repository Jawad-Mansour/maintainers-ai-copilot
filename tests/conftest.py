"""Shared pytest fixtures."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

FAKE_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
FAKE_CONV_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
FAKE_JWT_KEY = "test-signing-key-for-tests-only-32ch"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_secrets():
    from api.app.infra.vault import VaultSecrets

    data = {
        "postgres": {"user": "u", "password": "p", "db": "d", "host": "h", "port": "5432"},
        "openai": {"api_key": "sk-test-00000000000000000000"},
        "jwt": {"signing_key": FAKE_JWT_KEY},
        "minio": {"access_key": "ak", "secret_key": "sk", "endpoint": "http://minio:9000"},
        "langfuse": {"public_key": "pk", "secret_key": "sk", "host": "http://langfuse:3000"},
    }
    return VaultSecrets(data)


@pytest.fixture
def fake_user():
    from api.app.domain.models import UserOut

    return UserOut(
        id=FAKE_USER_ID,
        email="test@example.com",
        role="user",
        is_active=True,
        created_at=datetime(2024, 1, 1),
    )


@pytest.fixture
def fake_admin():
    from api.app.domain.models import UserOut

    return UserOut(
        id=FAKE_USER_ID,
        email="admin@example.com",
        role="admin",
        is_active=True,
        created_at=datetime(2024, 1, 1),
    )


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get.return_value = None
    r.set.return_value = True
    return r


@pytest.fixture
def mock_modelserver():
    ms = AsyncMock()
    ms.classify.return_value = ["bug"]
    ms.rerank.return_value = [0.9, 0.8, 0.7, 0.6, 0.5]
    return ms


@pytest.fixture
def mock_minio():
    return MagicMock()


@pytest.fixture
def mock_langfuse():
    lf = MagicMock()
    trace = MagicMock()
    gen = MagicMock()
    lf.trace.return_value = trace
    trace.generation.return_value = gen
    return lf
