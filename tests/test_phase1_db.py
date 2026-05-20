"""Phase 1 — Integration tests for DB migrations.

Uses testcontainers to spin up a real postgres+pgvector container,
runs all Alembic migrations, then asserts every table and index exists.
Requires Docker to be running.
"""

from __future__ import annotations

import pytest

pytest.importorskip("testcontainers", reason="testcontainers not installed")
pytest.importorskip("psycopg2", reason="psycopg2 not installed")

from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

REQUIRED_TABLES = {
    "users",
    "conversations",
    "messages",
    "memories",
    "chunks",
    "widgets",
    "audit_log",
}

REQUIRED_INDEXES = {
    "ix_memories_embedding_hnsw",
    "ix_chunks_embedding_hnsw",
    "ix_chunks_search_vector_gin",
}


@pytest.fixture(scope="module")
def pg_container():  # type: ignore[no-untyped-def]
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


def _run_migrations(db_url: str) -> None:
    """Run alembic upgrade head programmatically."""
    from alembic import command
    from alembic.config import Config

    # Point alembic at the api/ directory
    alembic_cfg = Config("api/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    # Override version locations to use db/migrations/
    alembic_cfg.set_main_option("version_locations", "db/migrations")
    command.upgrade(alembic_cfg, "head")


def test_all_tables_created(pg_container) -> None:  # type: ignore[no-untyped-def]
    """After migration, all required tables must exist."""
    import psycopg2

    url = pg_container.get_connection_url().replace("+psycopg2", "")
    _run_migrations(url)

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    missing = REQUIRED_TABLES - tables
    assert not missing, f"Missing tables after migration: {missing}"


def test_indexes_exist(pg_container) -> None:  # type: ignore[no-untyped-def]
    """After migration, HNSW and GIN indexes must exist."""
    import psycopg2

    url = pg_container.get_connection_url().replace("+psycopg2", "")

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    indexes = {row[0] for row in cur.fetchall()}
    conn.close()

    missing = REQUIRED_INDEXES - indexes
    assert not missing, f"Missing indexes after migration: {missing}"


def test_tsvector_trigger_exists(pg_container) -> None:  # type: ignore[no-untyped-def]
    """chunks_search_vector_update trigger must exist after migration."""
    import psycopg2

    url = pg_container.get_connection_url().replace("+psycopg2", "")

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute(
        "SELECT trigger_name FROM information_schema.triggers WHERE event_object_table = 'chunks'"
    )
    triggers = {row[0] for row in cur.fetchall()}
    conn.close()

    assert "chunks_search_vector_update" in triggers
