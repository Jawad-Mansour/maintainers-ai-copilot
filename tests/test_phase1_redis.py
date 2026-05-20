"""Phase 1 — Integration tests for Redis TTL behavior.

Uses testcontainers to spin up a real Redis 7 container.
Requires Docker to be running.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("testcontainers", reason="testcontainers not installed")
pytest.importorskip("redis", reason="redis package not installed")

from testcontainers.redis import RedisContainer  # type: ignore[import-untyped]


@pytest.fixture(scope="module")
def redis_container():  # type: ignore[no-untyped-def]
    with RedisContainer("redis:7-alpine") as r:
        yield r


def _get_client(container):  # type: ignore[no-untyped-def]
    import redis

    return redis.Redis(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(6379)),
        decode_responses=True,
    )


def test_redis_ping(redis_container) -> None:  # type: ignore[no-untyped-def]
    """Redis must respond to PING."""
    client = _get_client(redis_container)
    assert client.ping() is True


def test_conversation_ttl(redis_container) -> None:  # type: ignore[no-untyped-def]
    """Keys in DB 0 (conversation) must expire after TTL."""
    client = _get_client(redis_container)
    client.select(0)

    client.setex("conv:test-session", 2, "hello")  # 2s TTL for test speed
    assert client.get("conv:test-session") == "hello"

    time.sleep(3)
    assert client.get("conv:test-session") is None, "Conversation key should have expired"


def test_cache_ttl(redis_container) -> None:  # type: ignore[no-untyped-def]
    """Keys in DB 1 (API cache) must expire after TTL."""
    client = _get_client(redis_container)
    client.select(1)

    client.setex("cache:classify:abc123", 2, '{"label":"bug"}')
    assert client.get("cache:classify:abc123") is not None

    time.sleep(3)
    assert client.get("cache:classify:abc123") is None, "Cache key should have expired"


def test_two_logical_dbs_are_isolated(redis_container) -> None:  # type: ignore[no-untyped-def]
    """DB 0 (conversation) and DB 1 (cache) must be isolated."""
    c0 = _get_client(redis_container)
    c0.select(0)
    c0.set("shared-key", "from-db0")

    c1 = _get_client(redis_container)
    c1.select(1)

    assert c1.get("shared-key") is None, "DB 1 must not see DB 0 keys"
