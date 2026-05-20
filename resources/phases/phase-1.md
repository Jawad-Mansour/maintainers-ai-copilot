# Phase 1 — Infrastructure Foundation

**Status:** ✅ Complete (2026-05-20)
**Commit:** `feat: phase 1 — docker-compose, Vault, DB migrations, refuse-to-boot, Phase 1 tests`

---

## Goal

`docker-compose up` boots all services, health checks pass, Vault is initialized with all
secrets, the database schema is fully migrated (pgvector + all tables + indexes + triggers),
and the API container refuses to boot if Vault is unreachable.

---

## Why This Phase Exists

Every later phase depends on this infrastructure being solid:
- Phase 2 auth needs the `users` table and JWT key from Vault
- Phase 3 RAG needs pgvector, HNSW indexes, and the `chunks` table
- Phase 4 memory needs the `memories` table and Redis
- Phase 6 evals need the `evals` MinIO bucket
- Phase 7 ML needs the `models` MinIO bucket

Building infra first — before any application code — means every phase builds on a
stable, tested foundation rather than retroactively plumbing it in.

---

## Services Architecture

The compose stack has **12 containers** total: 10 long-running services + 2 one-shot
init jobs. The PLAN lists 10 named services; vault-init and minio-init are additional
setup utilities that exit 0 on success.

### Dependency Chain (boot order)

```
vault ──healthy──► vault-init ──exit 0──► migrate ──exit 0──► api
db ────healthy──► migrate                                       │
minio ─healthy──► minio-init ──exit 0───────────────────────►  │
redis ─healthy──────────────────────────────────────────────►  │
```

`docker-compose` enforces this via `condition: service_healthy` and
`condition: service_completed_successfully`. Nothing starts out of order.

### Service Table

| Service | Image | Healthcheck | Role |
|---------|-------|-------------|------|
| `db` | `pgvector/pgvector:pg16` | `pg_isready -U copilot -d copilot_db` | Primary store: all tables |
| `redis` | `redis:7-alpine` | `redis-cli ping` | Conversation cache (DB 0) + API cache (DB 1) |
| `minio` | `minio/minio:latest` | `curl /minio/health/live` | Model weights, eval reports, chunk snapshots |
| `vault` | `hashicorp/vault:1.17` | `vault status \| grep Initialized` | ALL secrets — no secret lives anywhere else |
| `vault-init` | `hashicorp/vault:1.17` | — (one-shot) | Writes 5 secret paths to Vault KV v2 |
| `minio-init` | `minio/mc:latest` | — (one-shot) | Creates 3 buckets: models, evals, chunks-snapshots |
| `migrate` | local (api image) | — (one-shot) | Runs `alembic upgrade head`, then exits 0 |
| `api` | local | `curl /health` | FastAPI backend |
| `modelserver` | local | `curl /health` | ML inference stub (real model in Phase 7) |
| `chatbot` | local | — | Streamlit admin stub (Phase 5) |
| `widget` | local (nginx) | — | React widget stub (Phase 5) |
| `host` | local (nginx) | — | Static demo HTML page |

---

## Files Created

### `docker-compose.yml`

**Every design decision explained:**

**Why `pgvector/pgvector:pg16` instead of `postgres:16`?**
The official postgres image does not include the pgvector extension. We need pgvector
for vector similarity search (HNSW index on embeddings). Using the pgvector pre-built
image avoids having to compile the extension from source inside the container.

**Why is the DB password hardcoded in docker-compose?**
This is the bootstrap problem: Vault holds the DB password, but the DB container
needs the password to initialize. The solution: the `db` container uses a fixed
dev-only password (`copilot_dev`). Vault also stores this same value. **The application
code never reads `POSTGRES_PASSWORD` from the environment** — it always fetches from
Vault. The hardcoded value is visible but not secret; it's only for local dev.

**Why `redis-server --save "" --appendonly no`?**
Disables RDB snapshots and AOF persistence. For this use case (short-lived conversation
cache with 24h TTL), disk persistence wastes I/O and storage. If Redis restarts, the
24h TTL means sessions re-build naturally within one conversation.

**Why does `migrate` use `condition: service_completed_successfully` (not `service_healthy`)?**
`migrate` is a one-shot job — it runs `alembic upgrade head` then exits 0. Health checks
are meaningless for containers that exit. `service_completed_successfully` is the correct
condition for one-shot jobs.

**Why does `api` depend on `minio-init` completing?**
The API boot guard checks that the `models` bucket exists (in Phase 7 this becomes a
classifier weights check). Even in Phase 1, the bucket must exist before the app starts.

**Why vault-init is a separate container, not part of vault?**
The Vault dev server starts immediately when the container boots, but it takes a moment
to be fully ready. A separate vault-init container can wait for the health check to pass
(`condition: service_healthy`) before running the init script. Baking init into the
vault entrypoint would require polling logic inside the Vault container.

---

### `db/vault-init.sh`

Runs inside a `hashicorp/vault:1.17` container after Vault is healthy.

```sh
vault secrets enable -version=2 -path=secret kv  # Enable KV v2
vault kv put secret/postgres user=... password=... db=... host=... port=...
vault kv put secret/openai api_key=sk-placeholder-...
vault kv put secret/jwt signing_key=super-secret-...
vault kv put secret/minio access_key=... secret_key=... endpoint=...
vault kv put secret/langfuse public_key=... secret_key=... host=...
```

**The 5 secret paths:**
| Path | Keys | Used by |
|------|------|---------|
| `secret/postgres` | user, password, db, host, port | migrate, api |
| `secret/openai` | api_key | api (LLM calls, embeddings) |
| `secret/jwt` | signing_key | api (fastapi-users JWT) |
| `secret/minio` | access_key, secret_key, endpoint | api, modelserver |
| `secret/langfuse` | public_key, secret_key, host | api (tracing) |

**Why placeholder values?**
The `openai.api_key` and `langfuse` keys are set to placeholder strings.
Before the demo, these are replaced with real values by running:
```sh
vault kv put secret/openai api_key="sk-real-key"
```
The app code reads from Vault — no other file changes needed.

**Why `2>/dev/null || true` on the enable command?**
If the vault-init container restarts (e.g., compose restart), re-enabling an already-
enabled KV engine returns an error. `|| true` makes the script idempotent.

---

### `db/minio-init.sh`

Creates the 3 required MinIO buckets:

| Bucket | Purpose |
|--------|---------|
| `models` | DistilBERT + TF-IDF model weights (uploaded after Phase 7 training) |
| `evals` | RAGAS eval reports (`eval_report.json`) written by Phase 6 CI |
| `chunks-snapshots` | Periodic snapshots of the chunks table for debugging |

`--ignore-existing` makes the script idempotent on restart.

---

### `eval_thresholds.yaml`

```yaml
ragas:
  faithfulness: 0.70
  answer_relevancy: 0.70
  context_precision: 0.65
retrieval:
  hit_at_5: 0.70
  mrr_at_10: 0.50
classifier:
  f1_macro: 0.75
```

**Why this file exists at this phase:**
The refuse-to-boot guard in `api/main.py` reads this file at startup. If the file
is missing or any value is ≤ 0, the API container exits non-zero. This prevents
a misconfigured app from silently skipping evaluation gates.

**Why these threshold values?**
- `faithfulness ≥ 0.70`: Industry baseline for RAG systems. Below 0.70 means the
  LLM is hallucinating more than 30% of the time — unacceptable for issue triage.
- `hit_at_5 ≥ 0.70`: The correct issue chunk must appear in top-5 results 70% of
  the time. Below this, the RAG pipeline is providing wrong context.
- `f1_macro ≥ 0.75`: Balanced across all 4 classes. A heavily imbalanced dataset
  (pandas-dev/pandas has more bug reports) makes macro F1 the right metric.
- The classifier metrics are filled with `0.0` placeholder comments — they get
  real values after Phase 7 training.

---

### `api/config.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    vault_addr: str
    vault_token: str
    db_host: str = "db"
    redis_host: str = "redis"
    minio_host: str = "minio"
```

**Why `extra="forbid"`?**
If an unknown environment variable matches a typo of a real setting name, pydantic
would silently ignore it in the default mode. `extra="forbid"` causes the app to
fail loudly instead of silently misconfiguring.

**Why only Vault coordinates and hostnames here?**
This is the bootstrap pattern. `.env` tells the app *where* Vault is. Vault tells
the app *everything else* (DB password, OpenAI key, JWT key, etc.).

**Why `db_host`, `redis_host`, `minio_host` here and not in Vault?**
These are service *hostnames* within the docker-compose network (`db`, `redis`, `minio`).
They're not secrets — they're topology. Putting topology in Vault would create a
circular dependency (can't connect to Vault without knowing where services are, but
services are where Vault tells you).

**Why `get_settings()` with a module-level singleton instead of `lru_cache`?**
`lru_cache` on a function that reads from environment variables can cause issues in
tests where you need to override env vars. The explicit singleton pattern gives clearer
control: call `_settings = None` to reset in tests.

---

### `api/app/infra/vault.py`

**`VaultSecrets` dataclass:**
Holds all fetched secrets as typed attributes. Routes and services never touch `hvac`
directly — they receive a `VaultSecrets` instance from `app.state.secrets`.

**`fetch_vault_secrets()` flow:**
1. Create `hvac.Client(url=vault_addr, token=vault_token)`
2. Call `client.is_authenticated()` — raises `RuntimeError` if Vault is down
3. Loop over 5 secret paths, read each with `kv.v2.read_secret_version`
4. Wrap all secrets in `VaultSecrets` and return

**Why raise `RuntimeError` instead of returning `None` or logging a warning?**
This is the refuse-to-boot contract. The error propagates to the lifespan, which
calls `sys.exit(1)`, causing Docker to mark the container as failed. A warning would
allow the app to boot in a broken state — that is exactly what the constitution
prohibits.

**`db_url` and `db_url_sync` properties:**
- `db_url`: `postgresql+asyncpg://...` — used by the async SQLAlchemy engine in the
  running application
- `db_url_sync`: `postgresql://...` — used by Alembic's synchronous migration engine

---

### `api/app/infra/db/`

Three files forming the database infrastructure layer:

**`base.py`** — Declares `Base = DeclarativeBase()`. All ORM models inherit from this.
Alembic's `env.py` imports `Base.metadata` to get the full schema for autogenerate.

**`session.py`** — `build_session_factory(db_url)` creates an async SQLAlchemy engine
and returns an `async_sessionmaker`. The factory is stored on `app.state` at startup
and injected into routes via `get_db()` dependency (Phase 2).

**`models.py`** — All 7 ORM models:

| Model | Table | Key columns |
|-------|-------|-------------|
| `User` | `users` | id (UUID), email, hashed_password, role (user/admin), is_active |
| `Conversation` | `conversations` | id, user_id (FK→users), created_at |
| `Message` | `messages` | id, conversation_id (FK), role (user/assistant), content |
| `Memory` | `memories` | id, user_id (FK), summary, **embedding vector(1536)**, created_at |
| `Chunk` | `chunks` | id, text, chunk_type (parent/child), parent_id (self-FK), label, source, **embedding vector(1536)**, **search_vector tsvector** |
| `Widget` | `widgets` | id, owner_id (FK), name, allowed_origins (TEXT[]), theme (JSONB) |
| `AuditLog` | `audit_log` | id, actor_id, action, target_id, diff (JSONB), created_at |

**Why `EMBEDDING_DIM = 1536`?**
OpenAI `text-embedding-3-small` produces 1536-dimensional vectors. This is hardcoded
as a module-level constant so it's easy to change if the embedding model changes.

**Why are ORM models in `app/infra/db/` and not in `app/domain/`?**
The constitution says `app/domain/` holds Pydantic models (the shared language between
layers). SQLAlchemy ORM models are infrastructure — they're coupled to the database
schema and only used by repositories and Alembic. Mixing them with domain Pydantic
models would break the separation of concerns.

**Why does `Memory` use a `vector(1536)` column?**
Long-term memory is retrieved by semantic similarity at the start of each conversation.
The embedding allows `SELECT ... ORDER BY embedding <-> $query_embedding LIMIT 5` to
find the most relevant memories for the current conversation context.

**Why does `Chunk` have both `embedding` (vector) and `search_vector` (tsvector)?**
This enables hybrid retrieval: dense search (cosine similarity on `embedding` via
pgvector HNSW) + sparse search (full-text relevance on `search_vector` via GIN index).
Both run in parallel via `asyncio.gather()` in Phase 3.

**Why the `search_vector` column is `Text` in the ORM model but `tsvector` in the DB?**
SQLAlchemy doesn't have a native `tsvector` type without extensions. The column is
declared as `Text` in the ORM so SQLAlchemy doesn't complain, but the migration
creates it as `tsvector` via raw SQL. A trigger automatically populates it on INSERT/UPDATE.

**HNSW index parameters:**
- `m = 16`: Number of bidirectional links per node. Higher = more recall, more memory.
  16 is the recommended default for cosine similarity workloads.
- `ef_construction = 64`: Build-time search width. Higher = better index quality,
  slower build. 64 is the recommended starting point.

---

### `api/alembic.ini`

Standard alembic configuration. Key settings:
- `script_location = alembic` — alembic reads `env.py` from `api/alembic/`
- Logging configured at WARN level (reduces migration noise)

---

### `api/alembic/env.py`

**What it does:**
1. Reads `VAULT_ADDR`, `VAULT_TOKEN`, `DB_HOST` from environment
2. Connects to Vault and fetches `secret/postgres`
3. Builds the DB URL: `postgresql://<user>:<password>@<host>:<port>/<db>`
4. Imports all models via `import app.infra.db.models` so `Base.metadata` is populated
5. Runs alembic migrations using a synchronous SQLAlchemy connection (psycopg2)

**Why does `migrate` use `psycopg2` (sync) while the `api` uses `asyncpg` (async)?**
Alembic's migration engine is synchronous. It uses SQLAlchemy's synchronous `engine_from_config`.
The running application uses `asyncpg` for non-blocking async database I/O. Both talk to
the same PostgreSQL — they just use different drivers. `psycopg2-binary` is installed in the
API image specifically for migrations.

**Why does env.py fetch secrets from Vault instead of reading a hardcoded DB URL?**
Keeping the Vault pattern consistent. The migrate container has `VAULT_ADDR` and
`VAULT_TOKEN` — it fetches the DB password the same way the API does. This means the
DB password never appears in plaintext in any config file or environment variable
outside of Vault.

---

### `db/migrations/0001_enable_pgvector.py`

```python
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

Must run before `0002` because the `chunks.embedding` and `memories.embedding` columns
use the `vector` type. `IF NOT EXISTS` makes it idempotent.

---

### `db/migrations/0002_core_tables.py`

Creates all 7 tables. Key non-obvious choices:

**Why are `embedding` columns added via raw SQL?**
SQLAlchemy's `op.create_table()` doesn't know about the `vector` type. Trying to pass
`pgvector.sqlalchemy.Vector` to `sa.Column()` in a migration fails. The workaround:
create the column as a placeholder (`Text`) inside `create_table`, immediately drop it,
then add it back via raw `ALTER TABLE ... ADD COLUMN embedding vector(1536)`.

**Why does `chunks.embedding` have a `DEFAULT array_fill(0, ARRAY[1536])::vector`?**
During bulk ingestion, rows are inserted in two steps: first the text, then the
embedding is updated after the OpenAI API call. The zero vector is a placeholder that
prevents a NOT NULL constraint violation during the intermediate state. The default is
dropped after column creation so future inserts must provide a real embedding.

**The tsvector trigger:**
```sql
CREATE OR REPLACE FUNCTION chunks_search_vector_trigger() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', coalesce(NEW.text, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER chunks_search_vector_update
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_search_vector_trigger();
```
Every INSERT or UPDATE on `chunks` automatically sets `search_vector` from `text`.
This means the application never manually writes to `search_vector` — it just inserts
text and the DB handles the FTS index automatically.

---

### `api/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_eval_thresholds()   # Raises RuntimeError → sys.exit(1) if missing/zero
    secrets = fetch_vault_secrets(...)   # Raises RuntimeError → sys.exit(1) if Vault down
    app.state.secrets = secrets
    yield
```

**Refuse-to-boot contract (NON-NEGOTIABLE from constitution):**
If either check fails, `sys.exit(1)` is called inside the lifespan before `yield`.
uvicorn propagates the exit code. Docker marks the container as failed (exit code 1).
`docker-compose up` reports the service as unhealthy.

This prevents the app from running in a broken state where:
- Vault is down but the app serves requests without being able to fetch secrets
- Evaluation thresholds are zeroed out (disabled) but CI still passes

**Why `sys.exit(1)` instead of raising an exception?**
Raising an exception inside the lifespan context manager causes uvicorn to print a
traceback but still try to continue. `sys.exit(1)` is a hard stop that guarantees
the process exits with a non-zero code.

**Why is `eval_thresholds.yaml` checked at boot, not at CI time?**
Both are needed. The YAML file is committed — if someone commits zeros, the CI eval
step catches it. The boot guard is a second line of defense: if the file is deployed
without thresholds, the container refuses to run rather than silently skipping evals.

---

### `api/app/api/routes/health.py`

```python
@router.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.0"})
```

Used by:
1. `docker-compose` healthcheck: `curl -f http://localhost:8000/health`
2. Kubernetes/load-balancer readiness probe (Phase 8)
3. Phase 1-T smoke test

Does not check downstream services (DB, Redis, Vault) — that's a liveness vs
readiness distinction. A full dependency health check is added in Phase 2.

---

### `api/dependencies.py`

Stub DI functions for Phase 1. `get_db()` and `get_redis()` raise
`NotImplementedError` — they're replaced with real implementations in Phase 2.

**Why define them as stubs now?**
So that route files written in Phase 2 can import `from dependencies import get_db`
without the file not existing. Consistent import paths from day one.

---

### `api/Dockerfile`

```dockerfile
FROM python:3.12-slim
RUN apt-get install -y gcc libpq-dev curl
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Why `gcc` and `libpq-dev`?**
`psycopg2-binary` requires `libpq` to compile (it links against libpq.so). Without
these system packages, `pip install psycopg2-binary` fails inside the container.

**Why `curl` installed?**
The docker-compose healthcheck for `api` uses `curl -f http://localhost:8000/health`.
`python:3.12-slim` doesn't include curl by default.

**Same image used for `migrate` service:**
The `migrate` service in docker-compose uses the same image but overrides CMD to
`alembic upgrade head`. This avoids maintaining a separate Dockerfile for migrations.

---

### Modelserver stub (`modelserver/main.py`)

4 endpoints returning mock data:

| Endpoint | Mock response | Phase that replaces it |
|----------|--------------|------------------------|
| `GET /health` | `{"status":"ok","mode":"mock"}` | Stays as-is |
| `POST /classify` | `{"label":"bug","confidence":0.0}` | Phase 7 (DistilBERT) |
| `POST /rerank` | Dummy decreasing scores | Phase 3 (cross-encoder) |
| `POST /ner` | `{"entities":[]}` | Phase 4 (spaCy) |

**Why mocks now?**
The RAG pipeline (Phase 3) and agent (Phase 4) call these endpoints. Building them
against real models from the start would block Phase 3 on Phase 7 completing. Mocks
let all phases develop in parallel and Phase 7 swaps in real models at the end.

---

### Widget + Host stubs

**`widget/Dockerfile`:** Switched from multi-stage Vite build to `nginx:alpine`
serving a static placeholder HTML. Phase 5 restores the full Vite build.

**`host/index.html`:** Static demo page with a placeholder where the widget will be
embedded. The embed tag is shown as a comment so the demo structure is visible.

**`chatbot/main.py`:** Minimal Streamlit page showing a "Phase 5" placeholder message.
The full admin panel is built in Phase 5.

---

## Issues Encountered & Fixed

### Pre-commit hook failures (first commit attempt)

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| `ruff I001` (import order) | 6 files had unsorted imports after initial write | `uv run ruff check . --fix` auto-fixed all |
| `ruff UP035` (deprecated imports) | Used `from typing import Sequence` instead of `collections.abc` | Auto-fixed by ruff |
| `ruff UP007` (union types) | Used `Union[str, None]` instead of `str \| None` | Auto-fixed by ruff |
| `ruff E501` (line too long) | Migration line 155 chars: `ALTER TABLE chunks ADD COLUMN embedding...` | Split into f-string concatenation |
| `ruff B008` (Depends in defaults) | Stub `get_db(settings = Depends(...))` — B008 forbids function calls in defaults | Simplified stubs to `get_db() -> None` |
| `ruff SIM117` (nested with) | `with patch(...): with pytest.raises(...)` | Merged into `with patch(...), pytest.raises(...)` |
| `ruff F401` (unused imports) | `importlib`, `sys`, `os` imported but unused in test files | Auto-fixed by ruff |
| `mypy misc` (subclassing Any) | Service packages (pydantic-settings, sqlalchemy) not in root `.venv` — mypy can't find types | Excluded `api/` and `tests/` from root mypy; each service runs mypy independently in CI |
| `mypy unused-ignore` on `lifespan` | `# type: ignore[type-arg]` was wrong code — real error was `no-untyped-def` (missing return type) | Added `-> AsyncGenerator[None, None]` return type annotation; removed wrong ignore |
| `mypy unused-ignore` on `get_db`/`get_redis` in `dependencies.py` | `# type: ignore[return]` is not needed — raising an exception satisfies `-> None` | Removed the `# type: ignore` comments |
| `mypy` not excluding `api/` and `tests/` | Pre-commit mypy hook excluded `chatbot/modelserver/widget/host/` but not `api/` or `tests/` | Added `api` and `tests` to the mypy `exclude` regex in `.pre-commit-config.yaml` |

### Phase 1-T unit test failures

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `ModuleNotFoundError: No module named 'hvac'` | `hvac` only in `api/requirements.txt`, not in root dev deps — `pytest` runs in root `.venv` | Added `hvac>=2.3.0` to `[dependency-groups] dev` in `pyproject.toml` |
| `ModuleNotFoundError: No module named 'config'` | `api/main.py` does `from config import get_settings` (bare import). `api/` was not on `sys.path` | Added `pythonpath = ["api"]` to `[tool.pytest.ini_options]` |
| `ModuleNotFoundError: No module named 'fastapi'` | Same root cause — `fastapi` only in `api/requirements.txt` | Added `fastapi>=0.115.0` to root dev deps |
| `ModuleNotFoundError: No module named 'pydantic_settings'` | Same root cause | Added `pydantic-settings>=2.0` to root dev deps |
| `ModuleNotFoundError: No module named 'yaml'` | `pyyaml` only in `api/requirements.txt` | Added `pyyaml>=6.0` to root dev deps |
| Integration tests: 2 skipped (not 7 collected) | `pytest.importorskip("redis", ...)` skipped because `redis` package not in root dev deps | Added `redis>=5.0`, `psycopg2-binary>=2.9`, `sqlalchemy[asyncio]>=2.0`, `alembic>=1.13.0` to root dev deps |

### Phase 1-T docker-compose failures

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `Bind for 0.0.0.0:5432 failed: port is already allocated` | Local PostgreSQL installation already using port 5432 on the host | Changed `POSTGRES_PORT=5432` → `POSTGRES_PORT=5433` in `.env` (host-side port only; internal Docker network still uses 5432) |
| Migration fails: `sqlalchemy.exc.ArgumentError: 'SchemaItem' object expected, got <class 'type'>` | `sa.Column.__class__.__mro__[0]` evaluates to Python's built-in `type` class, not a SQLAlchemy type. SQLAlchemy's `create_table()` passes column arguments through `_init_items()` which expects a `SchemaItem`, not the `type` metaclass | Replaced the broken placeholder with `sa.Text, nullable=True` — the column is dropped immediately after `create_table` and re-added as `vector(1536)` via raw SQL, so the placeholder type is irrelevant |
| `docker.errors.DockerException: Error while fetching server API version` | Docker Desktop was not running (engine stopped after laptop was left overnight) | Restarted Docker Desktop; waited for "Engine running" status in the bottom-left status bar |

### The migration bug in detail

**Broken code (original):**
```python
sa.Column(
    "embedding",
    sa.Column.__class__.__mro__[0],  # placeholder — see raw SQL below
),
```

`sa.Column.__class__` is `type` (the metaclass of all Python classes).
`type.__mro__[0]` is also `type`. So this passes the `type` built-in as
the SQLAlchemy column type argument, which SQLAlchemy rejects.

**Fixed code:**
```python
sa.Column("embedding", sa.Text, nullable=True),  # dropped and re-added via raw SQL below
```

`sa.Text` is a valid SQLAlchemy type. The column is dropped on the next line:
```python
op.drop_column("memories", "embedding")
op.execute(f"ALTER TABLE memories ADD COLUMN embedding vector({EMBEDDING_DIM}) NOT NULL")
```
So the placeholder type (`Text`) never exists in the final schema.

---

## Architecture Decisions Made in This Phase

**D-bootstrap: Why not put the DB password in `.env`?**
The `.env` is committed as `.env.example`. Putting real secrets there leaks them.
Using Vault means the only secret needed to start everything is the Vault token —
and that's already in `.env` as the bootstrap credential.

**D-vault-kv-v2: Why KV v2 and not KV v1?**
KV v2 supports versioned secrets and check-and-set operations. In production, you
can rotate secrets without downtime by incrementing the version. KV v1 is legacy.

**D-compose-health: Why healthchecks on every service?**
Without healthchecks, `depends_on` only waits for the container to start, not for
the service inside to be ready. PostgreSQL takes ~2 seconds to accept connections after
the container starts. Without the healthcheck, `migrate` would try to connect before
PostgreSQL is ready and fail.

**D-redis-dbs: Why two logical DBs (0 and 1) instead of key prefixes?**
Two DBs provides strict isolation: `FLUSHDB` on DB 1 (cache) doesn't touch DB 0
(conversations). Key prefixes are convention-only and can be violated. DB-level
isolation is enforced by Redis.

**D-one-shot-init: Why separate vault-init/minio-init containers vs. entrypoint scripts?**
The Vault and MinIO images don't include the tools needed to run our init scripts
(the Vault image doesn't have `mc`, the MinIO image doesn't have `vault`). Separate
containers allow using the right image for each init task. Using entrypoint scripts
would require custom images.

---

## Acceptance Criteria (Phase 1-T)

### Unit tests (no Docker required)
- [x] `pytest tests/test_phase1_boot_guard.py -v` → **5 passed**
  - `test_vault_fetch_succeeds_when_authenticated` ✅
  - `test_vault_fetch_raises_when_unauthenticated` ✅
  - `test_eval_thresholds_passes_when_valid` ✅
  - `test_eval_thresholds_raises_when_zero` ✅
  - `test_eval_thresholds_raises_when_file_missing` ✅

### Full stack (requires Docker Desktop running)
- [ ] `docker-compose up --wait` exits 0 — all health checks pass
- [ ] Vault responds: `vault status | grep "Initialized.*true"`
- [ ] All 5 secrets readable from Vault after vault-init
- [ ] `alembic current` inside migrate shows `0002 (head)`
- [ ] All 7 tables exist in PostgreSQL
- [ ] HNSW indexes on `memories.embedding` and `chunks.embedding` exist
- [ ] GIN index on `chunks.search_vector` exists
- [ ] tsvector trigger fires on `chunks` insert
- [ ] MinIO: 3 buckets exist (`models`, `evals`, `chunks-snapshots`)
- [ ] Redis: `redis-cli ping` → PONG
- [ ] `GET http://localhost:8000/health` → `{"status":"ok","version":"0.1.0"}`
- [ ] Boot guard: stopping Vault and restarting `api` → container exits non-zero
- [ ] Boot guard: `eval_thresholds.yaml` with `faithfulness: 0` → container exits non-zero

### Integration tests (requires Docker Desktop running)
- [ ] `pytest tests/test_phase1_db.py tests/test_phase1_redis.py -v` → 7 passed
  - `test_all_tables_created` — all 7 tables exist after migration
  - `test_indexes_exist` — HNSW + GIN indexes present
  - `test_tsvector_trigger_exists` — trigger fires on chunks insert
  - `test_redis_ping` — Redis responds to PING
  - `test_conversation_ttl` — DB 0 keys expire after TTL
  - `test_cache_ttl` — DB 1 keys expire after TTL
  - `test_two_logical_dbs_are_isolated` — DB 0 and DB 1 are isolated
