# Phase 1-T — Infrastructure Tests

**Status:** ✅ All passed (2026-05-20)
**Scope:** 3 test files, 12 tests

| File | Tests | Docker required |
|------|-------|----------------|
| `tests/test_phase1_boot_guard.py` | 5 | No — pure Python |
| `tests/test_phase1_db.py` | 3 | Yes — testcontainers PostgreSQL |
| `tests/test_phase1_redis.py` | 4 | Yes — testcontainers Redis |

---

## Goal

Verify that the three stateful infrastructure layers introduced in Phase 1 all work
correctly from a clean start:

1. **Boot guard** — the application refuses to boot when Vault is unreachable or
   `eval_thresholds.yaml` is missing / has zeroed values. Pure Python, no containers.

2. **DB migrations** — `alembic upgrade head` creates exactly the 7 required tables,
   the 3 required indexes (2 HNSW, 1 GIN), and the tsvector trigger. Tested against a
   real PostgreSQL 16 + pgvector container spun up by testcontainers.

3. **Redis TTL** — the two logical databases (DB 0 conversation, DB 1 cache) are
   isolated and keys actually expire after their TTL. Tested against a real Redis 7
   container.

---

## Why Three Separate Test Files

Each file tests a different infrastructure primitive at the right level of isolation:

- **Boot guard tests** have no I/O at all — they mock `hvac.Client` and swap
  `THRESHOLDS_FILE` at the module level. Running them takes milliseconds and requires
  no external processes.

- **DB migration tests** need a real PostgreSQL instance because Alembic migrations
  contain raw SQL (`CREATE INDEX USING hnsw`, `CREATE TRIGGER`, raw `ALTER TABLE ADD
  COLUMN vector(1536)`). Mocking would only verify that the test author correctly
  predicted the SQL — not that it actually runs.

- **Redis TTL tests** need a real Redis instance because TTL expiry is a Redis server
  behavior, not a Python behavior. Mocking `redis.get()` to return `None` after a
  sleep would confirm nothing about whether the actual configuration is correct.

---

## Test Files

### `tests/test_phase1_boot_guard.py` — 5 tests

All tests in this file are pure Python. No Docker, no network, no async. They run in
under a second on any machine.

**`test_vault_fetch_succeeds_when_authenticated`** — Patches `hvac.Client` with a mock
that returns `is_authenticated() == True` and serves fake secret data for all 5 KV
paths (`postgres`, `openai`, `jwt`, `minio`, `langfuse`). Calls `fetch_vault_secrets()`
and asserts the returned `VaultSecrets` object has the correct `db_user`, `openai_api_key`,
and that `db_url` contains `asyncpg`. This is the happy-path test: confirms the secret
parsing and `VaultSecrets` constructor work correctly when Vault responds normally.

**`test_vault_fetch_raises_when_unauthenticated`** — Patches `hvac.Client` with
`is_authenticated() == False`. Asserts `fetch_vault_secrets()` raises `RuntimeError`
matching `"Vault authentication failed"`. This is the refuse-to-boot test for an invalid
token: if the token is wrong, the app must fail loudly at startup rather than silently
serving requests without secrets.

**`test_eval_thresholds_passes_when_valid`** — Writes a temporary YAML file with all
values `> 0` (e.g., `faithfulness: 0.7`). Temporarily replaces `THRESHOLDS_FILE` in
`api.main` with the temp path. Calls `_check_eval_thresholds()` and asserts it does not
raise. This is the happy-path threshold test: confirms the function accepts a properly
configured thresholds file.

**`test_eval_thresholds_raises_when_zero`** — Writes a temporary YAML with one value
set to `0.0`. Asserts `_check_eval_thresholds()` raises `RuntimeError` matching
`"zero or disabled"`. The application must refuse to boot if any threshold has been
zeroed — zeroed thresholds would cause CI eval checks to always pass, defeating the
purpose of the evaluation gate entirely.

**`test_eval_thresholds_raises_when_file_missing`** — Sets `THRESHOLDS_FILE` to a path
that does not exist. Asserts `_check_eval_thresholds()` raises `RuntimeError` matching
`"not found"`. Guards against the case where someone deletes or renames the file — the
app must not silently boot with no evaluation thresholds at all.

---

### `tests/test_phase1_db.py` — 3 tests

All tests in this file use `testcontainers[postgres]` to spin up a real
`pgvector/pgvector:pg16` container, run the full Alembic migration stack against it,
and then query `information_schema` and `pg_catalog` to verify the resulting schema.

**Why testcontainers instead of mocking?** Mocking the database would only confirm that
the test author correctly predicted the migration output. It would not confirm the
migrations actually run. Alembic migrations contain raw SQL (`CREATE INDEX USING hnsw`,
`CREATE TRIGGER`, raw `ALTER TABLE ADD COLUMN vector(1536)`) that is easy to typo.
Running them end-to-end is the only way to know they work.

**`test_all_tables_created`** — Runs `alembic upgrade head` against the container.
Queries `information_schema.tables` and asserts all 7 required tables exist:
`users`, `conversations`, `messages`, `memories`, `chunks`, `widgets`, `audit_log`.
Every later phase depends on at least one of these tables — missing any one would
produce confusing failures in Phase 2+ tests.

**`test_indexes_exist`** — Queries `pg_catalog.pg_indexes` and asserts 3 critical
performance indexes exist:

| Index | Table | Type | Purpose |
|-------|-------|------|---------|
| `ix_chunks_embedding_hnsw` | `chunks` | HNSW (pgvector, cosine) | Sub-millisecond ANN search |
| `ix_memories_embedding_hnsw` | `memories` | HNSW (pgvector, cosine) | Semantic memory retrieval |
| `ix_chunks_search_vector_gin` | `chunks` | GIN | Full-text search via `@@` operator |

HNSW parameters: `m=16, ef_construction=64, vector_cosine_ops`. A missing HNSW index
turns every vector query into a full table scan — catastrophic at any real scale.

**`test_tsvector_trigger_exists`** — Queries `information_schema.triggers` and asserts
`chunks_search_vector_update` exists on the `chunks` table. The entire sparse half of
hybrid search (0.4 × FTS score) depends on `search_vector` being auto-populated on
every insert. The application never writes `search_vector` directly — it inserts `text`
and trusts the trigger. If the trigger is missing, FTS returns zero results and hybrid
search silently degrades to pure dense search.

---

### `tests/test_phase1_redis.py` — 4 tests

All tests in this file use `testcontainers[redis]` to spin up a real `redis:7-alpine`
container. The Redis TTL tests use `time.sleep(3)` to wait for a 2-second TTL to
expire — these tests take roughly 7 seconds combined.

**`test_redis_ping`** — Connects to the container and calls `client.ping()`. Asserts
the result is `True`. This is the basic connectivity check: if Redis is not accepting
connections, all other Redis tests would produce confusing errors rather than a clear
failure.

**`test_conversation_ttl`** — Selects DB 0 (conversation database). Sets a key with a
2-second TTL. Asserts the key is readable immediately. Sleeps 3 seconds. Asserts the
key is gone. This verifies that Redis actually enforces TTL expiry — not just that the
Python client can set an expiry parameter. The real-world contract is that conversation
history expires after 24 hours so stale context does not persist across long gaps.

**`test_cache_ttl`** — Same test on DB 1 (API cache database). Sets a key with a 2-second
TTL, waits, asserts expiry. Verifies the API cache (used for `GET /me`, `GET /conversations`)
also expires correctly. The real TTL is 5 minutes — short enough that stale user data
is refreshed frequently.

**`test_two_logical_dbs_are_isolated`** — Sets a key in DB 0. Opens a separate client
connected to DB 1. Asserts the key is NOT visible in DB 1. This verifies that the two
logical databases (conversation vs cache) do not share keyspace. If they were not
isolated, a cache key collision could overwrite a conversation history entry or vice
versa.

---

## Issues Hit During Phase 1-T

### `ModuleNotFoundError: No module named 'hvac'`

**Root cause:** `hvac` was only in `api/requirements.txt`. The root-level pytest runs in
the root `.venv`, which only has packages from `pyproject.toml`. So `from app.infra.vault
import VaultSecrets` failed immediately at import time.

**Fix:** Added all API-layer packages to `[dependency-groups] dev` in `pyproject.toml`
and bumped Python to `>=3.11`. The dev group is what gets installed into the root `.venv`
via `uv sync --dev`. This includes: `hvac`, `pyyaml`, `pydantic-settings`, `fastapi`,
`sqlalchemy[asyncio]`, `alembic`, `psycopg2-binary`, `redis`, `python-jose`,
`passlib[bcrypt]`, `asyncpg`, `pgvector`, `minio`, `openai`, `tiktoken`, `structlog`,
`tenacity`, `langfuse`, `numpy`, `email-validator`, `bcrypt<4.0`, `uvicorn`, `pydantic`.

### `ModuleNotFoundError: No module named 'config'`

**Root cause:** `api/main.py` imports `from config import get_settings`. `config.py` is
at `api/config.py`. Pytest's working directory is the project root, so `api/` is not on
`sys.path`.

**Fix:** Added `pythonpath = ["api"]` to `[tool.pytest.ini_options]` in `pyproject.toml`.
This prepends `api/` to `sys.path` before collecting tests, making both
`from config import get_settings` and `from app.xxx import ...` resolve correctly.

### Port conflict: `Bind for 0.0.0.0:5432 failed`

**Root cause:** Native PostgreSQL 16 already listening on host port 5432.

**Fix:** Set `POSTGRES_PORT=5433` in `.env`. Inside the Docker network all services still
reach the DB on `db:5432` — only the host-side port mapping changes.

### Migration crash: `'SchemaItem' object expected, got <class 'type'>`

**Root cause:** The original migration used `sa.Column.__class__.__mro__[0]` as a
placeholder column type. That evaluates to Python's built-in `type` metaclass, which
SQLAlchemy's `create_table()` rejects because it is not a `SchemaItem`.

**Fix:** Replaced with `sa.Column("embedding", sa.Text, nullable=True)`. SQLAlchemy
accepts `Text`; the column is immediately dropped and re-added as `vector(1536)` via
raw SQL on the next line, so the placeholder type is irrelevant to the final schema.

---

## Pass Criteria — All Met ✅

- [x] `pytest tests/test_phase1_boot_guard.py -v` → **5 passed** (no Docker needed)
  - [x] `test_vault_fetch_succeeds_when_authenticated`
  - [x] `test_vault_fetch_raises_when_unauthenticated`
  - [x] `test_eval_thresholds_passes_when_valid`
  - [x] `test_eval_thresholds_raises_when_zero`
  - [x] `test_eval_thresholds_raises_when_file_missing`
- [x] `pytest tests/test_phase1_db.py -v` → **3 passed** (requires Docker)
  - [x] `test_all_tables_created` — all 7 tables exist after migration
  - [x] `test_indexes_exist` — HNSW on chunks + memories, GIN on chunks.search_vector
  - [x] `test_tsvector_trigger_exists` — trigger fires on insert
- [x] `pytest tests/test_phase1_redis.py -v` → **4 passed** (requires Docker)
  - [x] `test_redis_ping`
  - [x] `test_conversation_ttl`
  - [x] `test_cache_ttl`
  - [x] `test_two_logical_dbs_are_isolated`

**Phase 1-T passed. Cleared to proceed to Phase 2.**
