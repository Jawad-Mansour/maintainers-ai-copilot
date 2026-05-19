# Maintainer's AI Copilot — Complete Implementation Plan

**Deadline:** Thursday May 21, 2026 @ 12:00 PM (~44 hours from start)
**Submission tag:** `v0.1.0-week7`
**Constraint:** ML/DL phase is last; all other phases must be fully functional with mock ML endpoints before Phase 7.

---

## Timeline Overview

| Phase | Name | When |
|-------|------|------|
| 0 | Repo, tooling, scaffolding | Tuesday evening |
| 0-T | Test Phase 0 | Tuesday evening |
| 1 | Infrastructure (compose, Vault, DB, Redis, MinIO) | Tuesday night |
| 1-T | Test Phase 1 | Tuesday night |
| 2 | API shell, auth, exception hierarchy, observability | Wednesday morning |
| 2-T | Test Phase 2 | Wednesday morning |
| 3 | Advanced RAG pipeline | Wednesday midday |
| 3-T | Test Phase 3 | Wednesday midday |
| 4 | Chatbot agent + memory | Wednesday afternoon |
| 4-T | Test Phase 4 | Wednesday afternoon |
| 5 | Embeddable widget + Streamlit admin | Wednesday evening |
| 5-T | Test Phase 5 | Wednesday evening |
| 6 | Evaluations + CI gates | Wednesday night |
| 6-T | Test Phase 6 | Wednesday night |
| 7 | ML/DL track (connect Colab weights, replace mocks) | Thursday morning |
| 7-T | Test Phase 7 | Thursday morning |
| 8 | Integration, docs, polish, tag | Thursday morning → 12pm |
| 8-T | Final smoke test + submission check | Thursday → 12pm |

**Rule:** Do NOT move to the next phase until the current phase's test phase passes. A failing test in Phase N caught early costs 10 minutes. The same failure caught in Phase N+3 costs hours.

Start Colab fine-tuning (Phase 7 training) in the background **during Phase 3** so weights are ready by Phase 7.

---

## Phase 0 — GitHub Repo + Tooling Setup

**Goal:** Empty repo exists, all tooling configured, team can clone and run pre-commit.

### Tasks
- [ ] `git init`, initial commit with `.gitignore` (Python, Node, Docker, secrets)
- [ ] Add `.gitignore` entries: `.env`, `*.pem`, `*.key`, `__pycache__/`, `*.pyc`, `.venv/`, `node_modules/`, `dist/`, `*.egg-info/`, `eval_report.json`
- [ ] Create `.env.example` with only: `VAULT_ADDR`, `VAULT_TOKEN`, `POSTGRES_PORT`, `REDIS_PORT`, `MINIO_PORT`, `API_PORT`, `CHATBOT_PORT`, `WIDGET_PORT`, `LANGFUSE_PORT`
- [ ] Initialize `uv` project (`uv init`), add `pyproject.toml` with ruff + mypy config
- [ ] Configure ruff: `line-length=100`, `select=["E","F","I","UP","B","SIM"]`, `target-version="py312"`
- [ ] Configure mypy: `strict=true`, `ignore_missing_imports=true`
- [ ] Install pre-commit: hooks for ruff, mypy, gitleaks (secret scanning)
- [ ] Create top-level directory scaffold (empty `__init__.py` where needed):
  ```
  maintainers-ai-copilot/
  ├── api/
  │   ├── app/
  │   │   ├── api/routes/
  │   │   ├── services/
  │   │   ├── repositories/
  │   │   ├── domain/
  │   │   ├── infra/
  │   │   ├── tools/
  │   │   └── prompts/
  │   ├── main.py
  │   ├── config.py
  │   ├── dependencies.py
  │   └── Dockerfile
  ├── chatbot/
  │   └── Dockerfile
  ├── widget/
  │   └── Dockerfile
  ├── modelserver/
  │   └── Dockerfile
  ├── host/
  │   └── Dockerfile
  ├── db/
  │   └── migrations/
  ├── evals/
  ├── notebooks/
  ├── docker-compose.yml
  ├── .env.example
  ├── .gitignore
  ├── pyproject.toml
  └── .pre-commit-config.yaml
  ```
- [ ] Push to GitHub, set branch protection on `main` (require PR, no force push)

### Tests (Phase 0)
- Smoke: `pre-commit run --all-files` passes on empty scaffold
- Smoke: `uv run ruff check .` exits 0
- Smoke: `uv run mypy .` exits 0 (no sources yet = trivially passes)

### Documentation
- Create `resources/PROGRESS.md` — log phase completions here as we go

---

## Phase 1 — Infrastructure Foundation

**Goal:** `docker-compose up` boots all 10 services, health checks pass, Vault initialized, DB schema migrated, refuse-to-boot guard in place.

### Services (docker-compose.yml)
| Service | Image | Purpose |
|---------|-------|---------|
| `db` | postgres:16 + pgvector | primary store |
| `redis` | redis:7-alpine | short-term memory + API cache |
| `minio` | minio/minio | model artifacts, eval reports, chunks snapshots |
| `vault` | hashicorp/vault:1.17 | ALL secrets |
| `migrate` | local build | runs `alembic upgrade head` then exits |
| `api` | local build | FastAPI backend |
| `chatbot` | local build | Streamlit admin |
| `modelserver` | local build | DistilBERT inference + NER |
| `widget` | local build | React embeddable widget |
| `host` | local build | demo host HTML page |

### Tasks

#### docker-compose.yml
- [ ] Define all 10 services with `depends_on` + `condition: service_healthy`
- [ ] `migrate` depends on `db` healthy, exits 0; `api` depends on `migrate` exited successfully
- [ ] Volume mounts: `./db/migrations:/app/alembic/versions`, `vault-data:/vault/data`, `minio-data:/data`
- [ ] Network: single `copilot-net` bridge
- [ ] All secrets passed from `.env` ONLY as `VAULT_ADDR`, `VAULT_TOKEN`, ports

#### Vault Setup
- [ ] Vault dev mode for local (`-dev -dev-root-token-id=root`)
- [ ] Vault init script (`db/vault-init.sh`): enable KV v2, write secrets:
  - `secret/openai` → `api_key`
  - `secret/postgres` → `password`, `user`, `db`
  - `secret/jwt` → `signing_key`
  - `secret/langfuse` → `public_key`, `secret_key`
  - `secret/minio` → `access_key`, `secret_key`

#### Database Schema (Alembic)
- [ ] `alembic init` in `db/`, configure `env.py` to read DB URL from Vault via hvac
- [ ] Migration 0001: enable pgvector extension
- [ ] Migration 0002: core tables
  ```sql
  -- users (fastapi-users managed)
  -- conversations(id, user_id, created_at)
  -- messages(id, conversation_id, role, content, created_at)
  -- memory(id, user_id, embedding vector(1536), summary text, created_at)
  -- chunks(id, text, embedding vector(1536), label text, source text,
  --         parent_id uuid, search_vector tsvector, metadata jsonb, created_at)
  -- widgets(id, owner_id, allowed_origins text[], theme jsonb,
  --          greeting text, enabled_tools text[], created_at)
  -- audit_log(id, actor_id, action text, target_id uuid, diff jsonb, created_at)
  ```
- [ ] HNSW index on `chunks.embedding`, `memory.embedding`
- [ ] GIN index on `chunks.search_vector`
- [ ] tsvector trigger on `chunks(text)` → `search_vector`

#### Redis
- [ ] Two logical DBs: DB 0 (conversation history, TTL 24h), DB 1 (API response cache, TTL 5min)

#### MinIO
- [ ] Init script creates three buckets: `models`, `evals`, `chunks-snapshots`

#### Health Checks
- [ ] `db`: `pg_isready`
- [ ] `redis`: `redis-cli ping`
- [ ] `minio`: `mc ready local`
- [ ] `vault`: `vault status`
- [ ] `api`: `GET /health` returns `{"status":"ok"}`

#### Refuse-to-boot Guard
- [ ] `api` lifespan: if Vault unauthenticated → `raise RuntimeError` → container exits non-zero
- [ ] `api` lifespan: validate eval_thresholds.yaml exists and all values > 0

### Tests (Phase 1)
- Integration: `testcontainers` spin up postgres+pgvector, run all Alembic migrations, assert all tables exist
- Integration: `testcontainers` spin up redis, verify TTL behavior (set key with TTL, wait, assert expired)
- Unit: mock hvac client, assert lifespan raises `RuntimeError` when `is_authenticated()` returns False
- Unit: mock hvac client, assert lifespan raises if `eval_thresholds.yaml` missing

### Documentation
- Start `ARCH.md`: infrastructure section (services, data flow diagram ASCII)

---

## Phase 2 — API Shell, Auth, Observability

**Goal:** Authenticated API with two roles, structured logging, Langfuse tracing, redaction layer, exception hierarchy, all endpoints returning correct HTTP codes.

### Tasks

#### FastAPI App Structure
- [ ] `main.py`: create FastAPI app, attach lifespan, mount routers
- [ ] `config.py`: pydantic-settings `Settings` class (Vault coords + non-secret config only, `extra="forbid"`)
- [ ] `dependencies.py`: `get_settings()` (lru_cache), `get_db()`, `get_redis()`, `get_openai()`, `get_current_user()`, `require_admin()`

#### Authentication (fastapi-users)
- [ ] Install fastapi-users with SQLAlchemy async backend
- [ ] Two roles: `user` and `admin` (stored in `users.role` column)
- [ ] JWT bearer token, secret from Vault `jwt.signing_key`
- [ ] Routes: `POST /auth/register`, `POST /auth/login`, `GET /users/me`
- [ ] Admin-only routes protected with `require_admin` dependency

#### Layered Architecture
- [ ] `app/api/routes/` → calls only `app/services/` (no direct DB access in routes)
- [ ] `app/services/` → calls only `app/repositories/` and `app/infra/` adapters
- [ ] `app/repositories/` → SQLAlchemy async sessions only
- [ ] `app/domain/` → pure dataclasses/Pydantic schemas, no I/O
- [ ] `app/infra/` → adapters: OpenAI, Redis, MinIO, Vault, Langfuse clients

#### Exception Hierarchy
- [ ] `app/domain/exceptions.py`: base `CopilotError`, subclasses: `NotFoundError`, `AuthorizationError`, `ClassifierError`, `RAGError`, `LLMError`, `RateLimitError`
- [ ] Single exception handler at API boundary: maps domain exceptions → HTTP status codes
- [ ] Infrastructure exceptions caught in `app/infra/` adapters and re-raised as domain exceptions

#### Redaction Layer
- [ ] `app/infra/redact.py`: before any log/trace/memory write, redact PII patterns (email, token, API key regex)
- [ ] All structlog log calls pass through `redact()` before writing
- [ ] All Langfuse span payloads pass through `redact()` before sending

#### Structured Logging
- [ ] structlog configured: JSON renderer, `request_id` injected via middleware
- [ ] Request ID middleware: generate UUID per request, bind to structlog context
- [ ] Log levels: DEBUG (dev), INFO (prod)

#### Langfuse Integration
- [ ] `app/infra/langfuse_client.py`: wrap Langfuse SDK, read keys from `app.state`
- [ ] Decorator / context manager for tracing LLM calls with input/output/latency
- [ ] Traces include: `conversation_id`, `user_id`, `tool_name`, `model`, `tokens`

#### Routes (stub implementations)
- [ ] `GET /health` → `{"status":"ok","version":"0.1.0"}`
- [ ] `POST /chat` → stub SSE stream (returns placeholder text)
- [ ] `POST /issues/classify` → stub returns `{"label":"bug","confidence":0.0}`
- [ ] `POST /issues/ingest` → stub returns `{"chunks_stored":0}`
- [ ] `GET /memory` → stub returns `[]`
- [ ] All routes require `Authorization: Bearer <token>` except `/health` and `/auth/*`

#### Tenacity Retry Config
- [ ] `app/infra/retry.py`: `@retry(wait=wait_exponential(min=1, max=10), stop=stop_after_attempt(3))` decorator for all external I/O (OpenAI, Langfuse, MinIO)

### Tests (Phase 2)
- Unit: test exception handler maps each domain exception to correct HTTP code
- Unit: test redaction strips email addresses, Bearer tokens, sk-* patterns from log dicts
- Unit: test `get_settings()` raises `ValidationError` when required field missing
- Unit: test `get_settings()` raises `ValidationError` when unknown field present (extra=forbid)
- Integration: `pytest-asyncio` + `respx` mock OpenAI — POST `/auth/register`, then `/auth/login`, assert JWT returned
- Integration: assert protected route returns 401 without token, 200 with valid token
- Integration: assert `/users/me` returns `role: "user"` for regular user
- Integration: assert admin-only route returns 403 for regular user, 200 for admin
- Integration: assert structlog output is valid JSON (parse with `json.loads`)

### Documentation
- `ARCH.md`: auth section, exception hierarchy diagram, layered architecture
- `SECURITY.md`: JWT config, Vault pattern, redaction guarantees

---

## Phase 3 — Advanced RAG Pipeline

**Goal:** Ingest GitHub issues corpus, hybrid retrieval with reranking and HyDE returns relevant chunks, metadata filtering working, modelserver mock endpoints running.

### Tasks

#### Corpus Ingestion
- [ ] `api/app/services/ingest.py`: `ingest_issues(issues: list[GitHubIssue]) → int`
- [ ] Preprocessing pipeline (7 steps, see D-preprocess in DECISIONS.md):
  1. Strip HTML (BeautifulSoup4)
  2. Normalize whitespace
  3. Deduplicate exact hashes
  4. Drop empty (title + body < 10 chars)
  5. Drop dual-labeled (issues with 2+ conflicting labels)
  6. Keep code blocks intact (fence-aware splitting)
  7. Truncate to `MAX_CHARS=2048`
- [ ] Hierarchical chunking: parent 1024 tokens, child 256 tokens; child stores `parent_id`
- [ ] Embed child chunks via OpenAI `text-embedding-3-small` (batch up to 100)
- [ ] Bulk insert into `chunks` table using `asyncpg` COPY for speed
- [ ] Generate tsvector via SQL trigger (already set up in Phase 1)
- [ ] Store `label` and `source` metadata columns per chunk

#### Hybrid Retrieval
- [ ] `api/app/services/rag.py`: `retrieve(query: str, label: str | None, top_k: int = 5) → list[Chunk]`
- [ ] Dense retrieval: embed query → pgvector HNSW ANN
- [ ] Sparse retrieval: `plainto_tsquery` FTS
- [ ] Parallel: `asyncio.gather(dense_search(), sparse_search())`
- [ ] Hybrid scoring SQL:
  ```sql
  SELECT id, text, parent_id,
    (0.6 * (1 - embedding <-> $1) + 0.4 * ts_rank(search_vector, plainto_tsquery($2))) AS score
  FROM chunks
  WHERE ($3::text IS NULL OR label = $3 OR source = 'docs')
  ORDER BY score DESC LIMIT 20
  ```
- [ ] Metadata filter: pass `label` from classifier result (Phase 4 will supply it via tool)

#### Cross-Encoder Reranking
- [ ] `api/app/infra/reranker.py`: load `cross-encoder/ms-marco-MiniLM-L-6-v2` via modelserver HTTP call
- [ ] Rerank top-20 → return top-5 (`context_top_k=5`)
- [ ] Modelserver mock endpoint: `POST /rerank` → returns dummy scores `[0.9, 0.8, 0.7, 0.6, 0.5]` for any input

#### HyDE Query Transformation
- [ ] `api/app/services/hyde.py`: call GPT-4o-mini with `prompts/hyde.md` → generate hypothetical document
- [ ] 50/50 blend: embed both original query and hypothetical doc, average the vectors
- [ ] `prompts/hyde.md`: system prompt instructing model to write a GitHub issue that would answer the query

#### Chunk Snapshot to MinIO
- [ ] After each retrieval, save retrieved chunks JSON to MinIO `chunks-snapshots/<conversation_id>/<timestamp>.json`
- [ ] Used for offline debugging and RAGAS evaluation input

#### Modelserver (mock)
- [ ] `modelserver/main.py`: FastAPI app with:
  - `POST /classify` → `{"label":"bug","confidence":0.0,"probabilities":{...}}` (mock)
  - `POST /ner` → `{"entities":[]}` (mock)
  - `POST /rerank` → `{"scores":[0.9,0.8,0.7,0.6,0.5]}` (mock)
  - `GET /health` → `{"status":"ok"}`
- [ ] These mocks will be replaced with real model inference in Phase 7

#### Route Implementation
- [ ] `POST /issues/ingest`: accepts `list[GitHubIssueCreate]`, calls `ingest_service.ingest_issues()`, returns `{"chunks_stored": N}`
- [ ] `POST /rag/search` (internal, admin-only): accepts query + label filter, returns top-5 chunks with scores

### Tests (Phase 3)
- Unit: test preprocessor each step independently (strip HTML, dedup, drop empty, etc.)
- Unit: test chunker produces parent/child pairs with correct token counts (≤1024/≤256)
- Unit: `respx` mock OpenAI embeddings endpoint → assert `ingest_issues()` calls embed in batches of ≤100
- Unit: test hybrid scorer formula: given mock dense score 0.8 and sparse score 0.6, assert combined = 0.72
- Unit: `respx` mock modelserver `/rerank` → assert reranker returns top-5 from top-20
- Unit: test HyDE: `respx` mock OpenAI chat completion → assert averaged vector has correct dimension (1536)
- Integration: `testcontainers` postgres+pgvector, ingest 10 synthetic issues, assert `SELECT COUNT(*) FROM chunks` = expected child count
- Integration: ingest 10 issues, run hybrid retrieval, assert top result is semantically relevant (cosine sim > 0.5 to query)
- Integration: assert metadata filter reduces result set (label='bug' excludes 'feature' chunks)

### Documentation
- `ARCH.md`: RAG pipeline section with chunking diagram
- `DECISIONS.md`: already has D-meta, D-preprocess — verify content matches implementation

---

## Phase 4 — Chatbot Agent + Memory

**Goal:** GPT-4o-mini tool-calling agent streams SSE responses, manages Redis short-term memory, persists semantic long-term memory in pgvector, writes audit log.

### Tasks

#### Agent Core
- [ ] `api/app/services/agent.py`: `run_agent(conversation_id, user_id, message, tools) → AsyncIterator[str]`
- [ ] System prompt loaded from `prompts/system.md`
- [ ] Tool loop: call GPT-4o-mini with tools, handle `tool_use` response, call tool, inject result, continue until text response
- [ ] SSE streaming: yield tokens as `data: <token>\n\n`, end with `data: [DONE]\n\n`

#### Tools
- [ ] `api/app/tools/classify_issue.py`: calls modelserver `POST /classify` → returns label + confidence
- [ ] `api/app/tools/rag_search.py`: calls `rag.retrieve(query, label)` → returns formatted chunks
- [ ] `api/app/tools/extract_entities.py`: calls modelserver `POST /ner` → returns entity list
- [ ] `api/app/tools/summarize_issue.py`: calls GPT-4o-mini with `prompts/summarize.md` → returns summary

#### Tool Definitions (OpenAI function schema)
- [ ] Each tool has name, description, parameters (JSON Schema), and implementation function

#### Short-term Memory (Redis)
- [ ] Conversation history stored in Redis list: `conv:{conversation_id}` → `[{"role":"user","content":"..."},...]`
- [ ] TTL: 24h (`CONVERSATION_TTL=86400`)
- [ ] On each turn: prepend system prompt + last N messages (context window budget)
- [ ] Trim history to last 20 messages to avoid context overflow

#### Long-term Memory (pgvector)
- [ ] After each assistant response: embed the summary → store in `memory` table
- [ ] `api/app/services/memory.py`: `store_memory()`, `retrieve_relevant_memories(query, user_id, top_k=3)`
- [ ] On each turn: retrieve top-3 relevant memories, inject into system prompt as `[Memory]: ...`
- [ ] Redact memory content before storing (pass through `redact()`)

#### API Response Cache (Redis DB 1)
- [ ] Cache `POST /issues/classify` responses: key = `classify:{sha256(text)}`, TTL 5min
- [ ] Skip cache on cache miss, store on hit

#### Audit Log
- [ ] Write to `audit_log` table on: role change, memory write, widget config change, conversation deletion
- [ ] Schema: `actor_id`, `action`, `target_id`, `diff` (JSON), `created_at`

#### Route Implementation
- [ ] `POST /chat`: accept `{"message": str, "conversation_id": uuid}`, return SSE stream
- [ ] `GET /memory`: return user's stored memories (paginated)
- [ ] `DELETE /conversations/{id}`: delete conversation + Redis key + write audit log entry

#### Langfuse Tracing
- [ ] Each agent run creates a Langfuse trace with: `conversation_id`, `user_id`, `model`, total tokens
- [ ] Each tool call creates a child span with: `tool_name`, `input`, `output`, `latency_ms`

### Tests (Phase 4)
- Unit: mock OpenAI + all tool functions → assert agent calls tools in correct order for a "classify this issue" message
- Unit: test Redis conversation history: store 25 messages, assert only last 20 retrieved
- Unit: test TTL: mock Redis, assert `EXPIRE` called with `86400` after each history write
- Unit: test memory retrieval: mock pgvector query → assert top-3 memories injected into prompt
- Unit: test redaction applied before `store_memory()` (inject PII, assert stored text is redacted)
- Unit: test audit log written on conversation deletion (mock DB session, assert `INSERT INTO audit_log`)
- Unit: test API cache: classify same text twice, assert OpenAI/modelserver called only once
- Integration: `testcontainers` + mock OpenAI (respx) → full chat turn: message in → SSE tokens out → history stored in Redis
- Integration: assert SSE stream ends with `data: [DONE]`
- Integration: `GET /memory` returns stored memories for authenticated user, not for other user

### Documentation
- `ARCH.md`: agent loop diagram, memory architecture
- `DECISIONS.md`: already has D-memory, D-rag, D-llm — verify all accurate

---

## Phase 5 — Embeddable Widget + Streamlit Admin

**Goal:** React widget embeds in host page via `<script>` tag, Streamlit admin interface for chat + memory inspection + widget config.

### Tasks

#### React Widget (Vite + Tailwind)
- [ ] `widget/src/App.tsx`: chat iframe, postMessage resize channel
- [ ] Tailwind CSS with PurgeCSS → bundle ≤5KB
- [ ] `widget/src/components/ChatWindow.tsx`: message list, input box, SSE streaming display
- [ ] Connect to `POST /chat` API with Authorization header (JWT passed via data attribute)
- [ ] Loader script `widget/public/widget.js`:
  ```js
  (function() {
    const s = document.currentScript;
    const widgetId = s.dataset.widgetId;
    const iframe = document.createElement('iframe');
    iframe.src = `${API_BASE}/widget?widget_id=${widgetId}`;
    iframe.style.cssText = 'position:fixed;bottom:20px;right:20px;width:380px;height:600px;border:none;z-index:9999;';
    document.body.appendChild(iframe);
    window.addEventListener('message', (e) => {
      if (e.data.type === 'resize') iframe.style.height = e.data.height + 'px';
    });
  })();
  ```
- [ ] CSP header on widget iframe: `frame-ancestors 'self' <allowed_origins from DB>`
- [ ] CORS: read `allowed_origins` from `widgets` table for the given `widget_id`

#### Widget API Routes
- [ ] `GET /widget/config/{widget_id}` → returns theme, greeting, enabled_tools (public, no auth)
- [ ] `PUT /widget/config/{widget_id}` → update widget config (admin only), writes audit log
- [ ] `POST /widgets` → create new widget (admin only)

#### Host Demo Page
- [ ] `host/index.html`: minimal HTML page with `<script data-widget-id="..." src="http://localhost:<WIDGET_PORT>/widget.js">` tag
- [ ] Shows: "Paste this script tag into your page" demo

#### Streamlit Admin (`chatbot/`)
- [ ] `chatbot/app.py`: Streamlit app with 3 pages:
  1. **Chat**: text input → call `POST /chat` API → stream response → display message history
  2. **Memory Inspector**: call `GET /memory` → display memory cards with timestamps + summaries
  3. **Widget Config**: form to update widget `theme`, `greeting`, `allowed_origins`, `enabled_tools`
- [ ] Streamlit auth: login form → call `POST /auth/login` → store JWT in `st.session_state`
- [ ] Handle SSE streaming from Streamlit: use `requests` with `stream=True`, yield chunks

#### Origin Allowlisting
- [ ] On each widget iframe request: read `allowed_origins` from `widgets` table
- [ ] Set `Content-Security-Policy: frame-ancestors 'self' <origins>` header
- [ ] Set CORS `Access-Control-Allow-Origin` to requesting origin if in allowlist, else 403

### Tests (Phase 5)
- Unit: test `widget.js` loader script creates iframe with correct src (jsdom or manual string assertion)
- Unit: test CORS middleware returns 403 for origin not in `allowed_origins`, 200 for allowed origin
- Unit: test CSP header contains exactly the origins from DB (mock DB query)
- Unit: test widget config `PUT` writes to audit log (mock DB, assert INSERT)
- Integration: `testcontainers` db → create widget → `GET /widget/config/{id}` → assert theme returned
- Integration: test origin allowlisting end-to-end with two different origin headers

### Documentation
- `ARCH.md`: widget embedding section, CSP/CORS diagram
- `SECURITY.md`: origin allowlisting policy, CSP frame-ancestors enforcement

---

## Phase 6 — Evaluations + CI Gates

**Goal:** RAGAS evaluation suite passes committed thresholds, eval report stored in MinIO, CI pipeline runs on every PR.

### Tasks

#### Golden Evaluation Sets
- [ ] `evals/golden_rag.json`: 25 question-answer-context triples for RAG evaluation
- [ ] `evals/golden_classify.json`: 25 issue-label pairs (balanced across 4 classes) for classifier evaluation
- [ ] Both sets hand-crafted (not from training data)

#### RAGAS Evaluation
- [ ] `evals/eval_rag.py`:
  - Load golden set
  - For each question: call `rag.retrieve()` → get contexts
  - Call GPT-4o-mini for answer generation
  - Compute RAGAS metrics: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`
  - Write `eval_report.json` with all scores
  - Assert each metric ≥ threshold from `eval_thresholds.yaml`
- [ ] Upload `eval_report.json` to MinIO `evals/` bucket

#### Classifier Evaluation
- [ ] `evals/eval_classify.py`:
  - Load golden set
  - For each issue: call modelserver `POST /classify`
  - Compute macro-F1, per-class precision/recall
  - Assert macro-F1 ≥ `eval_thresholds.yaml:classifier_macro_f1`
  - Append results to `eval_report.json`

#### eval_thresholds.yaml
- [ ] `evals/eval_thresholds.yaml`:
  ```yaml
  rag_faithfulness: 0.75
  rag_answer_relevancy: 0.75
  rag_context_precision: 0.70
  rag_context_recall: 0.70
  classifier_macro_f1: 0.80
  ```
- [ ] Committed to repo; fail build if any threshold not met (Phase 8: fill with real numbers after Phase 7)

#### EVALS.md
- [ ] Document evaluation methodology, dataset sources, metric definitions, current scores

#### CI Pipeline (GitHub Actions)
- [ ] `.github/workflows/ci.yml`:
  - Trigger: PR to `main`, push to `main`
  - Jobs:
    1. `lint`: ruff + mypy
    2. `test-unit`: pytest unit tests (no containers)
    3. `test-integration`: pytest integration tests (testcontainers)
    4. `eval`: run `eval_classify.py` (mock modelserver in CI)
  - Fail PR if any job fails
  - Cache uv dependencies between runs
- [ ] `gitleaks` step in CI: fail if any secret detected

### Tests (Phase 6)
- Unit: test `eval_rag.py` fails if a metric is below threshold (inject mock RAGAS output below threshold)
- Unit: test `eval_classify.py` fails if macro-F1 < 0.80 (inject mock confusion matrix)
- Unit: test `eval_report.json` uploaded to MinIO after eval run (mock MinIO client)
- Integration: run full eval suite against mock modelserver, assert `eval_report.json` created with correct keys

### Documentation
- `EVALS.md`: complete (methodology, golden set construction, metric choice rationale)
- `RUNBOOK.md`: how to run evals locally and in CI

---

## Phase 7 — ML/DL Track (Connect Real Models)

**Goal:** Fine-tuned DistilBERT and spaCy NER replace mock modelserver endpoints. Three-way comparison documented.

> **Note:** Start Colab fine-tuning during Phase 3. By Phase 7, weights should be ready to download.

### Sub-phase 7a: Data Preparation (can start during Phase 3)
- [ ] `notebooks/01_data_prep.ipynb`:
  - Load GitHub issues dataset (source: GUIDELINES.md / assignment corpus)
  - Apply 7-step preprocessing pipeline
  - 4-class label mapping: bug / feature / docs / question
  - Split: sort by `created_at` → train = oldest 70%, val = next 15%, test = most recent 15%
  - Stratify within each time window to preserve class proportions
  - Test set is strictly more recent in time than train (assignment requirement — prevents data leakage)
  - Save as `train.jsonl`, `val.jsonl`, `test.jsonl` to MinIO `models/` bucket
  - Log dataset stats with W&B

### Sub-phase 7b: Classical ML Baseline
- [ ] `notebooks/02_classical_ml.ipynb`:
  - TF-IDF (max_features=50000, ngram_range=(1,2)) + Logistic Regression (C=1.0, max_iter=1000)
  - 5-fold cross-validation on train set
  - Report macro-F1 on test set
  - Log all metrics and confusion matrix to W&B
  - Save model artifact to MinIO `models/tfidf_lr.pkl`

### Sub-phase 7c: DistilBERT Fine-tuning (Google Colab T4)
- [ ] `notebooks/03_finetune_distilbert.ipynb` (run on Colab T4):
  - Model: `distilbert-base-uncased`
  - Full fine-tune (all layers)
  - Hyperparameters: lr=2e-5, batch=16, epochs=4, warmup_ratio=0.1
  - Loss: CrossEntropyLoss with inverse-frequency class weights
  - Optimizer: AdamW with weight decay=0.01
  - Evaluation: macro-F1 on val set after each epoch
  - Early stopping: patience=2
  - Log all to W&B (loss curves, per-epoch metrics, confusion matrix)
  - Save best checkpoint to MinIO `models/distilbert_classifier/`
  - Record SHA-256 hash of checkpoint → update `DECISIONS.md` D-deploy table

### Sub-phase 7d: LLM Zero-shot Baseline
- [ ] `notebooks/04_llm_zeroshot.ipynb`:
  - GPT-4o-mini zero-shot with structured output (JSON mode)
  - Prompt: classify issue into bug/feature/docs/question with confidence
  - Run on test set (sample 200 if cost concern)
  - Report macro-F1
  - Log to W&B

### Sub-phase 7e: Three-way Comparison
- [ ] `notebooks/05_comparison.ipynb`:
  - Table: TF-IDF+LR vs DistilBERT vs GPT-4o-mini
  - Metrics: macro-F1, per-class F1, inference latency, cost/call
  - W&B comparison report
  - Training plots exported to MinIO `evals/training_plots/`
  - Update `DECISIONS.md` D-deploy with actual numbers

### Sub-phase 7f: NER with spaCy + EntityRuler
- [ ] `notebooks/06_ner.ipynb`:
  - Install spaCy `en_core_web_sm`
  - EntityRuler patterns for code-shaped entities:
    - `REPO`: `{owner}/{repo}` pattern
    - `ISSUE_REF`: `#\d+` pattern
    - `VERSION`: `v\d+\.\d+(\.\d+)?` pattern
    - `FILEPATH`: `/[\w/.-]+\.\w+` pattern
  - Test on 20 sample issues
- [ ] `api/app/infra/ner.py`: `build_spacy_ner_pipeline() → spacy.Language`

### Sub-phase 7g: Replace Modelserver Mocks
- [ ] `modelserver/src/classify.py`: load DistilBERT from MinIO, `asyncio.to_thread()` for inference
- [ ] `modelserver/src/ner.py`: load spaCy pipeline, run EntityRuler
- [ ] `modelserver/src/rerank.py`: load `cross-encoder/ms-marco-MiniLM-L-6-v2` from HuggingFace cache
- [ ] `POST /classify`: real DistilBERT inference → label + confidence + probabilities
- [ ] `POST /ner`: real spaCy NER → entity list with spans and labels
- [ ] Model SHA-256 validation on startup: assert hash matches value from `DECISIONS.md`
- [ ] Update `eval_thresholds.yaml` with real DistilBERT numbers
- [ ] Re-run `evals/eval_classify.py` with real model, assert passes thresholds

### Sub-phase 7h: Summarizer
- [ ] `api/app/tools/summarize_issue.py`: call GPT-4o-mini with `prompts/summarize.md`
- [ ] `prompts/summarize.md`: system prompt asking for 2-sentence issue summary preserving code entity mentions
- [ ] Wire into agent tool loop

### Tests (Phase 7)
- Unit: test preprocessing pipeline on 5 hand-crafted cases (HTML, duplicates, empty, dual-label, code blocks)
- Unit: test EntityRuler patterns each match correct entity type (repo, issue ref, version, filepath)
- Unit: test model SHA-256 validation raises on wrong hash (mock file read)
- Unit: test `asyncio.to_thread()` wrapping (mock heavy function, assert runs in thread pool)
- Integration: `testcontainers` + modelserver container → `POST /classify` with real text → assert label in `{bug,feature,docs,question}`
- Integration: run `eval_classify.py` against real modelserver, assert macro-F1 ≥ 0.80
- Notebook: assert W&B run created and all expected metrics logged (check W&B API)

### Documentation
- `DECISIONS.md`: fill D-deploy table with actual F1/latency numbers
- `EVALS.md`: update with real classifier eval results and three-way comparison table

---

## Phase 8 — Integration, Documentation, Polish, Tag

**Goal:** All services work end-to-end from fresh clone, all required docs complete, submission tag applied.

### Tasks

#### End-to-End Integration Test
- [ ] Verify: `docker-compose up --build` from fresh clone → all health checks pass within 60s
- [ ] Verify: register user → login → get JWT → POST /chat → receive SSE stream
- [ ] Verify: admin creates widget → host page loads → widget appears → chat works
- [ ] Verify: Langfuse dashboard shows traces
- [ ] Verify: MinIO console shows eval_report.json, model artifacts

#### Required Documentation

- [ ] **ARCH.md** — final pass:
  - System diagram (ASCII or Mermaid): all 10 services + data flows
  - Auth flow: register → login → JWT → protected routes
  - RAG pipeline: ingest → chunk → embed → hybrid retrieve → rerank → HyDE → inject
  - Agent loop: user message → classify → rag_search → NER → summarize → response
  - Widget embedding: host → loader script → iframe → postMessage resize
  - Memory: Redis (short-term) + pgvector (long-term) + audit log

- [ ] **DECISIONS.md** — final pass:
  - Fill D-deploy with real training metrics
  - Verify all 18+ decisions documented with rationale

- [ ] **RUNBOOK.md** — complete:
  - Prerequisites (Docker, uv, make)
  - First-time setup (Vault init, MinIO buckets, DB migrate)
  - Start all services: `docker-compose up --build`
  - Run tests: `uv run pytest`
  - Run evals: `uv run python evals/eval_rag.py && uv run python evals/eval_classify.py`
  - Retrain model: link to Colab notebook
  - Rotate secrets: Vault KV update procedure
  - Common troubleshooting: Vault unreachable, DB migration failure, modelserver OOM

- [ ] **EVALS.md** — complete:
  - RAG evaluation: RAGAS metrics, golden set methodology, scores
  - Classifier evaluation: three-way comparison table with actual numbers
  - NER evaluation: entity precision/recall on 20 test cases
  - Thresholds rationale

- [ ] **SECURITY.md** — complete:
  - Threat model: who can access what
  - Secret management: Vault pattern, what's in .env vs Vault
  - Authentication: JWT, role-based access
  - PII redaction: what is redacted, where
  - Origin allowlisting: CORS + CSP
  - Audit log: what events are tracked
  - Secret scanning: gitleaks in pre-commit + CI

- [ ] **README.md**:
  - Project overview (1 paragraph)
  - Quick start: 3 commands to get running
  - Architecture overview (link to ARCH.md)
  - How to embed the widget (1 code block)
  - How to run evals (1 command)

#### Final Checks
- [ ] Run full test suite: `uv run pytest --tb=short -q` — all green
- [ ] Run ruff + mypy: zero errors
- [ ] Run gitleaks: no secrets detected
- [ ] Run evals: all metrics above thresholds
- [ ] `docker-compose up --build` from fresh clone: all services healthy

#### Submission
- [ ] Merge all work to `main`
- [ ] Apply tag: `git tag -a v0.1.0-week7 -m "Week 7 submission"`
- [ ] Push tag: `git push origin v0.1.0-week7`
- [ ] Verify GitHub Actions CI passes on `main`
- [ ] Submit GitHub repo URL

### Tests (Phase 8)
- E2E: full happy path from fresh `docker-compose up` (script that registers, logs in, chats, checks SSE)
- E2E: widget embed works (curl host page, assert script tag present, assert iframe loads)
- Smoke: all health check endpoints return 200
- Smoke: all 10 containers healthy after `docker-compose up --build`

---

## Testing Strategy Summary

| Layer | Tools | When |
|-------|-------|------|
| Unit tests | pytest + pytest-asyncio | Every phase |
| Mock external calls | respx (HTTP), factory_boy (fixtures) | Phases 2–7 |
| Database integration | testcontainers (postgres, redis) | Phases 1, 3, 4 |
| Container integration | testcontainers (modelserver) | Phase 7 |
| E2E | docker-compose + curl/httpx scripts | Phase 8 |
| Eval suite | RAGAS, scikit-learn metrics | Phases 6–7 |

**Run command:** `uv run pytest tests/ -m "not e2e" --tb=short -q`
**E2E:** `uv run pytest tests/e2e/ -m e2e`

---

## Documentation Cadence

After each phase, update these files:

| After Phase | Update |
|-------------|--------|
| 0 | `resources/PROGRESS.md` — Phase 0 done |
| 1 | `ARCH.md` infra section, `resources/PROGRESS.md` |
| 2 | `ARCH.md` auth section, `SECURITY.md` start, `resources/PROGRESS.md` |
| 3 | `ARCH.md` RAG section, `DECISIONS.md` verify D-meta/D-preprocess, `resources/PROGRESS.md` |
| 4 | `ARCH.md` agent section, `resources/PROGRESS.md` |
| 5 | `ARCH.md` widget section, `SECURITY.md` CORS/CSP, `resources/PROGRESS.md` |
| 6 | `EVALS.md` start, `RUNBOOK.md` eval section, `resources/PROGRESS.md` |
| 7 | `DECISIONS.md` D-deploy final numbers, `EVALS.md` complete, `resources/PROGRESS.md` |
| 8 | All docs final pass, `README.md`, tag |

---

## Key Numbers to Remember

| Parameter | Value |
|-----------|-------|
| Dense weight | 0.6 |
| Sparse weight | 0.4 |
| Reranker input | top-20 |
| Reranker output | top-5 |
| HyDE blend | 50/50 |
| Parent chunk | 1024 tokens |
| Child chunk | 256 tokens |
| Max chars (preprocess) | 2048 |
| Embedding model | text-embedding-3-small |
| LLM | gpt-4o-mini |
| Classifier | distilbert-base-uncased |
| Reranker | ms-marco-MiniLM-L-6-v2 |
| Conversation TTL | 86400s (24h) |
| Cache TTL | 300s (5min) |
| Short-term history | last 20 messages |
| Long-term memories per turn | top-3 |
| Golden RAG set | 25 QA pairs |
| Golden classifier set | 25 issue-label pairs |
| Training split | 70/15/15 |
| DistilBERT lr | 2e-5 |
| DistilBERT epochs | 4 |
| DistilBERT batch size | 16 |

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Colab T4 timeout during training | Save checkpoint every epoch to MinIO; resume from checkpoint |
| pgvector HNSW index slow on large corpus | Use `ef_construction=128, m=16` defaults; index after bulk insert |
| OpenAI rate limits during ingestion | Batch embeds, add tenacity retry, respect 3500 RPM limit |
| DistilBERT macro-F1 < 0.80 threshold | Fallback: increase epochs, adjust class weights; document in DECISIONS.md |
| Time runs out before Phase 8 | Phase 8 docs can be completed in parallel with Phase 7 training |
