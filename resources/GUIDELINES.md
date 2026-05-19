# GUIDELINES.md — Maintainer's AI Copilot
Synthesized from: AIE Bootcamp Coding Guidelines, Code Review Guidelines, Engineering Standards
Companion Guide, Week 3 & 4 Code Review Lessons Learned.
Only rules that apply to **this project** are included.

---

## 0. The One Rule That Beats All Others

**You must be able to defend every line in this repository.**
Every file, import, config flag, Docker directive, hyperparameter, and prompt word is a question
waiting to be asked. If you copied something without understanding it, go back and understand it
before the demo. "The AI suggested it" is not an answer.

---

## 1. Project Structure

```
maintainers-ai-copilot/
├── api/                     # FastAPI backend — entry point + lifespan
│   ├── main.py              # app init, lifespan, mount routers
│   ├── config.py            # pydantic-settings Settings class
│   ├── dependencies.py      # shared Depends() functions
│   ├── routes/              # one router file per resource
│   │   ├── chat.py
│   │   ├── classify.py
│   │   ├── ingest.py
│   │   └── memory.py
│   ├── services/            # business logic (no HTTP knowledge)
│   │   ├── agent.py
│   │   ├── rag.py
│   │   ├── classifier.py
│   │   ├── ner.py
│   │   └── summarizer.py
│   ├── tools/               # one file per agent tool
│   │   ├── rag_search.py
│   │   ├── classify_issue.py
│   │   └── extract_entities.py
│   ├── repositories/        # DB access layer
│   ├── models/
│   │   ├── db.py            # SQLAlchemy ORM models
│   │   └── schemas.py       # Pydantic request/response schemas
│   └── prompts/             # one .md file per prompt (versioned)
│       ├── system.md
│       ├── summarize.md
│       └── hyde.md
├── chatbot/                 # chatbot service
├── widget/                  # React embeddable widget
├── modelserver/             # DistilBERT + spaCy inference
├── db/
│   └── migrations/          # Alembic migrations
├── evals/                   # RAGAS + classifier golden sets
├── notebooks/               # Colab training notebooks
├── host/                    # nginx — demo host app for Friday (10th service)
├── docker-compose.yml
├── .env.example
└── .gitignore
```

**Rules:**
- One file, one responsibility. If you cannot describe a file's job in one sentence, split it.
- Never put more than one prompt template in a single file. Prefer `summarize.md` over a
  `prompts.py` with five templates.
- Notebooks stay in `notebooks/` — never in the project root.
- Frontend (widget) and backend (api) are separate top-level folders with separate dependencies.

---

## 2. Git Conventions

### Branch naming
```
feature/<short-description>
bugfix/<short-description>
refactor/<short-description>
chore/<short-description>
```
- Lowercase, hyphens only. 2–4 words.
- Never commit directly to `main`.

### Commit messages (Conventional Commits)
```
feat(rag): add hybrid retrieval with pgvector FTS
fix(classifier): handle empty issue body edge case
chore(docker): pin postgres to 16.3
```
- Imperative mood, under 72 characters, no trailing period.
- Types: `feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf` / `security`

### PR guidelines
- Title: `[FEATURE] Add cross-encoder reranking to RAG pipeline`
- Body template: Summary / Changes / Testing / Checklist
- < 400 lines of changes. One concern per PR.

---

## 3. Python Code Style

| Tool | Purpose | Config |
|---|---|---|
| **ruff** | Linter + formatter | `line-length = 100`, `select = ["E","F","I","B","UP","ASYNC","S"]` |
| **mypy** | Type checker | `strict = true` |

- **Type hints required on all function signatures.**
- 4 spaces, never tabs. Double quotes (ruff default).
- `snake_case` for variables/functions, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- No single-letter names outside short loops. Functions start with a verb: `fetch_`, `classify_`, `embed_`.
- Run `ruff check . && ruff format .` before every commit (pre-commit hook).

### Naming examples
```python
# Good
def embed_issue_chunks(chunks: list[Chunk]) -> list[np.ndarray]: ...
def classify_issue_type(title: str, body: str) -> ClassLabel: ...

# Bad
def process(data): ...
def do_stuff(x, y): ...
```

---

## 4. Configuration

**Architecture rule (non-negotiable): `.env` holds ONLY the Vault root token and service ports.
All secrets (OpenAI key, DB password, JWT signing key, MinIO credentials) are fetched from Vault
at startup — never read from `.env` by the application.**

```
# .env (only these — nothing else)
VAULT_ADDR=http://vault:8200
VAULT_TOKEN=root
POSTGRES_PORT=5432
REDIS_PORT=6379
MINIO_PORT=9000
API_PORT=8000
```

```python
# config.py — reads only non-secret config from .env
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",   # typo in .env = startup error, not silent None
    )
    # Vault location (from .env)
    vault_addr: str = "http://vault:8200"
    vault_token: str

    # Non-secret tunable config (safe to read from .env or defaults)
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    dense_weight: float = 0.6
    sparse_weight: float = 0.4
    reranker_top_k: int = 20
    context_top_k: int = 5
    conversation_ttl: int = 86_400   # 24h (D15)
    cache_ttl: int = 300             # 5min (D15)

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

**Secrets are fetched from Vault in `lifespan` and attached to `app.state` — see Section 6.**

- `extra="forbid"` — a typo like `VAUILT_TOKEN` causes a crash at startup, not a silent failure.
- No `os.getenv()` scattered across files. Import `get_settings()` everywhere.
- `grep -ri 'sk-' app/` and `grep -ri 'password' app/` must return zero matches outside Vault-reading code.
- App refuses to boot if Vault is unreachable.
- Commit `.env.example` with placeholder values. `.env` is in `.gitignore` always.

---

## 5. Async All the Way Down

Every step in this project is I/O: LLM calls, pgvector queries, Redis, GitHub API. One blocking call
in the request path freezes the entire event loop.

```python
# Wrong — lies about being async
@app.post("/chat")
async def chat(query: str):
    result = openai.chat.completions.create(...)  # blocks

# Right
@app.post("/chat")
async def chat(query: str):
    result = await openai_client.chat.completions.create(...)  # non-blocking
```

**Rules:**
- Use `httpx.AsyncClient` for all HTTP calls. Never use `requests` in a request path.
- Use `AsyncOpenAI` client (already decided: GPT-4o-mini, D13).
- Use SQLAlchemy 2.x async mode for all DB queries.
- Use `asyncio.gather()` when two or more I/O calls can run in parallel (e.g., dense + sparse search).
- Use `asyncio.to_thread()` for CPU-bound work: DistilBERT inference, spaCy NER, cross-encoder reranking.
- Never call `time.sleep()` in async code — use `await asyncio.sleep()`.

```python
# Parallel dense + sparse retrieval
dense_hits, sparse_hits = await asyncio.gather(
    vector_search(query_embedding, top_k=20),
    fts_search(query_text, top_k=20),
)

# CPU-bound inference off the event loop
label = await asyncio.to_thread(model.predict, features)
```

---

## 6. Dependency Injection & Lifespan

### Singletons — load once in lifespan, never per-request

```python
from contextlib import asynccontextmanager
import hvac

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # 1. Fetch all secrets from Vault first — refuse to boot if Vault is unreachable
    vault = hvac.Client(url=settings.vault_addr, token=settings.vault_token)
    if not vault.is_authenticated():
        raise RuntimeError("Vault is unreachable or token is invalid — refusing to boot")

    openai_api_key = vault.secrets.kv.v2.read_secret_version("openai")["data"]["data"]["api_key"]
    db_password    = vault.secrets.kv.v2.read_secret_version("postgres")["data"]["data"]["password"]
    jwt_secret     = vault.secrets.kv.v2.read_secret_version("jwt")["data"]["data"]["signing_key"]

    # 2. Build singletons using fetched secrets
    app.state.openai       = AsyncOpenAI(api_key=openai_api_key)
    app.state.engine       = create_async_engine(build_database_url(db_password))
    app.state.jwt_secret   = jwt_secret
    app.state.http_client  = httpx.AsyncClient(timeout=10.0)

    # 3. Load ML models (CPU, from MinIO artifacts)
    app.state.classifier   = load_distilbert_model(settings.model_path)
    app.state.ner_pipeline = build_spacy_ner_pipeline()

    # 4. Validate startup invariants
    _assert_model_sha256_matches(app.state.classifier, settings.model_card_path)
    _assert_eval_thresholds_nonzero(settings.eval_thresholds_path)

    yield

    # shutdown — dispose everything cleanly
    await app.state.http_client.aclose()
    await app.state.engine.dispose()

app = FastAPI(lifespan=lifespan)
```

**Per-process singletons (lifespan):** DB engine, ML models, embedding model, LLM client, HTTP
client, spaCy NLP pipeline, Redis pool, JWT secret.
**Per-request (yield in dependency):** DB session, transaction, current user context.

### Dependency pattern

```python
# dependencies.py
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session   # closes automatically after request, even on exception

def get_classifier(request: Request) -> DistilBertClassifier:
    return request.app.state.classifier

def get_openai(request: Request) -> AsyncOpenAI:
    return request.app.state.openai

# routes/classify.py
@router.post("/classify", response_model=ClassificationResponse)
async def classify_issue(
    body: ClassifyRequest,
    classifier: DistilBertClassifier = Depends(get_classifier),
    session: AsyncSession = Depends(get_session),
):
    ...
```

- Never construct a session or client inside a route function body.
- `Depends()` is how you inject DB sessions, the current user, clients, models. Always.

---

## 7. Database — PostgreSQL + SQLAlchemy + Alembic

**We do not write raw SQL. We write ORM classes + Alembic migrations.**

```python
# models/db.py
class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("chunks.id"))
    text: Mapped[str]
    embedding: Mapped[Vector] = mapped_column(Vector(1536))
    search_vector: Mapped[str]   # tsvector — GENERATED ALWAYS
    source: Mapped[str]
    issue_id: Mapped[Optional[int]]
    label: Mapped[Optional[str]]
    is_parent: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
```

**Migration workflow:**
```bash
alembic revision --autogenerate -m "add chunks table"
alembic upgrade head
```

- Every schema change gets an Alembic migration committed. "I deleted the volume" is not a migration.
- `upgrade()` applies the change; `downgrade()` reverts it. Know both.
- Verify persistence with psql CLI: restart the container, query the table, confirm rows survive.

---

## 8. API Contracts — Don't Leak the Database

- Every endpoint has a `response_model` that is **different from the ORM model**.
- Never return password hashes, internal flags, or raw DB row objects to the client.
- ORM model → Pydantic response schema at the API boundary.

```python
# schemas.py
class ChunkResponse(BaseModel):      # what the client sees
    id: uuid.UUID
    text: str
    source: str
    label: Optional[str]
    # No embedding vector — 1536 floats have no business in an HTTP response
    # No search_vector — internal Postgres column

class ClassificationResponse(BaseModel):
    label: Literal["bug", "feature", "docs", "question"]
    confidence: float
    entities: list[EntitySpan]
    summary: str
```

### HTTP status codes — use them correctly
| Code | Meaning | When to use |
|---|---|---|
| 200 | OK | Success with body |
| 201 | Created | New resource created |
| 400 | Bad Request | Malformed request |
| 401 | Unauthorized | No/invalid/expired token |
| 403 | Forbidden | Valid token, insufficient permission |
| 404 | Not Found | Resource doesn't exist |
| 422 | Unprocessable Entity | Pydantic validation failure (auto) |
| 500 | Internal Server Error | Unhandled server-side error |

**401 vs 403: these are not interchangeable.** A missing token = 401. A valid token trying a
forbidden action = 403. Getting this wrong tells reviewers the auth layer was copied, not understood.

---

## 9. Authentication

- JWTs travel in `Authorization: Bearer <token>` header. Not in the body.
- JWT payload: `user_id` + `exp`. Nothing sensitive — payloads are base64, not encrypted.
- Access tokens are short-lived. Know what a refresh token is and where it's stored.
- Every protected endpoint has a `Depends(get_current_user)` dependency — no naked routes.
- Secrets (JWT signing key, OpenAI key) live in Vault — never in `.env` or code. Fetched at startup.

---

## 10. Caching

### lru_cache — for deterministic, pure, in-process functions
```python
@lru_cache(maxsize=1)
def get_settings() -> Settings: ...

@lru_cache(maxsize=1)
def build_spacy_ner_pipeline() -> spacy.Language: ...
```

### Redis — for conversation state and API responses (D15)
```python
CONVERSATION_TTL = 86_400   # 24h — conversation history
CACHE_TTL = 300             # 5min — LLM API response cache
```

Document your TTL choice with a comment. "300 seconds" is a decision, not a default — be ready
to defend it.

---

## 11. Error Handling & Retries

### No bare except. Catch specific exceptions.
```python
# Wrong
try:
    result = await openai_client.chat.completions.create(...)
except:
    pass

# Right
try:
    result = await openai_client.chat.completions.create(...)
except openai.RateLimitError as e:
    logger.warning("rate_limit_hit", retry_after=e.retry_after)
    raise HTTPException(status_code=429, detail="Rate limit reached. Try again shortly.")
except openai.APIError as e:
    logger.error("openai_api_error", error=str(e))
    raise HTTPException(status_code=502, detail="LLM service temporarily unavailable.")
```

### Retries with exponential backoff (tenacity)
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    reraise=True,
)
async def call_external_api(url: str) -> dict: ...
```

### Tool failures — return structured errors, don't crash the agent
```python
class ToolError(BaseModel):
    error: str
    retryable: bool

async def rag_search(query: str) -> list[ChunkResult] | ToolError:
    try:
        return await retrieve_chunks(query)
    except asyncio.TimeoutError:
        return ToolError(error="vector store timeout", retryable=True)
```

**Timeouts on every external call:**
```python
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.get(url)
```

Never expose stack traces to clients. Log the full exception server-side, return a sanitized message.

---

## 12. Logging

**Never use `print()` for operational output.** Logs that only exist in stdout are gone the moment a
container restarts. Use structured logging.

```python
import structlog
log = structlog.get_logger()

async def run_rag_pipeline(query: str, user_id: str) -> list[ChunkResult]:
    log.info("rag.start", user_id=user_id, query_length=len(query))
    try:
        chunks = await retrieve_and_rerank(query)
        log.info("rag.success", user_id=user_id, chunks_returned=len(chunks))
        return chunks
    except Exception as e:
        log.exception("rag.failure", user_id=user_id, error=str(e))
        raise
```

**Log:** request IDs, user actions (classify, chat), tool calls, error messages + stack traces,
performance metrics (latency, cache hit/miss).
**Never log:** passwords, API keys, JWT tokens, full issue body if it could contain PII.

LLM tracing: use **Langfuse** (D16 decision) — not `print()`. Every GPT-4o-mini call becomes an
inspectable span.

---

## 13. Security

```
# .gitignore — must include from day 1
.env
.env.*
*.pem
*.key
__pycache__/
.venv/
*.py[cod]
.coverage
.pytest_cache/
.mypy_cache/
```

```
# .dockerignore
.git
.env
.env.*
__pycache__/
*.py[cod]
.venv/
tests/
notebooks/
```

- Secrets in Vault always — locally AND in production. `.env` holds only `VAULT_ADDR`, `VAULT_TOKEN`, and ports.
- If a secret is accidentally committed: **rotate it immediately** (assume compromised), then clean history.
- `gitleaks` pre-commit hook to block secret commits before they happen.
- Validate all user input at the API boundary with Pydantic. Never trust client-side validation alone.

---

## 14. Docker & Compose

- One service per container. The api, chatbot, widget, modelserver are each their own image.
- **Networks** let containers talk to each other by service name. **Volumes** persist data across restarts.
  These solve different problems — do not confuse them.
- No hardcoded URLs or ports in `docker-compose.yml`. Use env variables with defaults:
  ```yaml
  environment:
    DATABASE_URL: ${DATABASE_URL:-postgresql+asyncpg://postgres:postgres@db:5432/copilot}
    REDIS_URL: ${REDIS_URL:-redis://redis:6379}
  ```
- CORS middleware in FastAPI: required so the React widget (different origin) can call the API.
  Know which origins, methods, and headers are allowed — not `allow_all`.
- `depends_on` with `healthcheck` — the api container must wait for `db` to be ready, not just started.

---

## 15. ML / Training Standards

### Train/test split
```python
X_train, X_test, y_train, y_test = train_test_split(
    X, y,
    test_size=0.15,
    random_state=42,           # reproducibility
    stratify=y,                # preserve class balance across splits — mandatory for imbalanced data
)
```

`stratify=y` is not optional here. The pandas dataset has ~1,656 question samples vs ~500 docs —
forgetting `stratify` means your test set may under-represent a class.

### Metrics — interpret, don't just display
For 4-class classification, report **macro-F1** (primary) + per-class F1 + confusion matrix.
Be ready to answer:
- Which class is the model weakest on and why?
- What does a false positive in class X cost vs. a false negative?
- What is the baseline? (majority-class classifier or random classifier by proportion)

### Class weighting (D3)
```python
from torch.nn import CrossEntropyLoss
weights = torch.tensor([w_bug, w_feature, w_docs, w_question], dtype=torch.float32)
criterion = CrossEntropyLoss(weight=weights)
```
Defend the weight values — they come from inverse class frequency, not guesswork.

### No data leakage
- Evaluation golden set (25 issues) must NOT appear in the RAG index.
- TF-IDF vectorizer must be fit on training split only, then applied to test split.
- W&B sweep hyperparameter search must use a validation split, not the test split.

---

## 16. RAG Pipeline Standards

Be able to narrate the full pipeline end-to-end:

```
Ingestion (runs ONCE):
  load issues → clean → hierarchical chunk (256 child / 1024 parent tokens)
  → embed children with text-embedding-3-small → store in pgvector (HNSW index)
  → build FTS tsvector column on same table

Query (runs PER REQUEST):
  user query → HyDE (generate hypothetical answer → embed → 50/50 blend with query vector)
  → hybrid search: 0.6 × cosine similarity (pgvector) + 0.4 × ts_rank (FTS), top-20
  → cross-encoder rerank (ms-marco-MiniLM-L-6-v2) → top-5 parent chunks
  → GPT-4o-mini with retrieved context
```

**Know the numbers at every step:** retrieve 20, rerank to 5. Be ready to defend those numbers.

### pgvector metadata stored with each vector
```
id, parent_id, text, embedding (1536), search_vector (tsvector),
source, issue_id, label, created_at, is_parent
```

### Ingestion runs once; retrieval runs per-request
This is a common bug. Ingestion is a one-time script or startup job. The retrieval pipeline runs on
every chat turn. Don't confuse them.

---

## 17. Chatbot / Agent Standards

### Tools must have typed schemas + clear docstrings
```python
class RagSearchInput(BaseModel):
    query: str = Field(..., description="The user's question about the codebase or issues")
    top_k: int = Field(default=5, ge=1, le=20)

async def rag_search(input: RagSearchInput) -> list[ChunkResult]:
    """Search the indexed GitHub issues and documentation for relevant context.

    Use this when the user asks about past issues, bugs, features, or codebase knowledge.
    """
    ...
```
A vague docstring = the LLM guesses wrong about when to call the tool.

### Prompts live in source control
Every prompt is a versioned `.md` file in `api/prompts/`. The system prompt is not pasted into a
chat window the night before the demo. Prompt engineering is engineering — review it, version it,
defend every word.

### Streaming (SSE) for chat responses
Chat responses stream tokens to the widget as they are generated. This is why the UI feels
instantaneous. The mechanism is server-sent events — an open HTTP connection where the server
pushes `data: {...}\n\n` chunks. Know how to implement `StreamingResponse` in FastAPI.

---

## 18. Testing Standards

```python
# AAA pattern: Arrange - Act - Assert
def test_classify_issue_labels_bug_correctly():
    # Arrange
    classifier = DistilBertClassifier.from_config(test_config)
    issue = IssueInput(title="NullPointerException in DataFrame.merge", body="...")
    # Act
    result = classifier.predict(issue)
    # Assert
    assert result.label == "bug"
    assert result.confidence > 0.7
```

**What to test in this project:**
1. Pydantic schemas — valid and invalid inputs (cheap, high-value)
2. Tool logic — mock the LLM and external APIs, test the logic
3. RAG pipeline — mock embeddings, assert retrieval returns the right structure
4. One end-to-end happy path through the chatbot agent
5. CI golden set evaluation for classifier (macro-F1 > 0.80) and RAG (hit@5 > 0.80)

**Coverage target:** 80% on new code. 95% on auth and data mutation paths.

**Tests run automatically:** GitHub Actions on every push + PR. A test that does not run in CI does
not exist.

---

## 19. Pre-Demo Checklist

Derived from Week 3 & 4 code review checklists, adapted to this project.

### Structure
- [ ] Every file's job can be described in one sentence.
- [ ] No stray notebooks or CSVs in the project root.
- [ ] All prompts are in `api/prompts/`, one file each.
- [ ] `README.md` lets someone clone and run with `docker compose up` without asking me anything.

### Python & FastAPI
- [ ] `ruff check .` passes with zero errors.
- [ ] `mypy .` passes with zero errors.
- [ ] Every function has type hints.
- [ ] `Settings` class with `extra="forbid"` is the only place `os.getenv` runs.
- [ ] Every route uses `Depends()` — no globals or per-request client construction.
- [ ] Heavy resources (models, DB engine, HTTP client) load in `lifespan`, not on import.

### Async
- [ ] No `requests` library in any request path.
- [ ] No `time.sleep()` in any async function.
- [ ] Model inference goes through `asyncio.to_thread()`.
- [ ] Parallel I/O uses `asyncio.gather()`.

### Database
- [ ] Every schema change has an Alembic migration committed.
- [ ] I can restart the containers and confirm data survives (verified with psql).
- [ ] `response_model` on every endpoint differs from the ORM model.

### Security & Config
- [ ] `.env` is in `.gitignore`. No secrets committed anywhere.
- [ ] `.env.example` is committed with placeholder values.
- [ ] No hardcoded URLs or ports in `docker-compose.yml`.
- [ ] CORS origins are explicit — not `allow_all`.
- [ ] 401 for missing/expired token. 403 for insufficient permission.

### ML & RAG
- [ ] `stratify=y` in `train_test_split`.
- [ ] `random_state` set for reproducibility.
- [ ] Classifier metrics explained, not just displayed. Baseline comparison exists.
- [ ] Evaluation golden set (25 issues) is NOT in the RAG index.
- [ ] I can narrate the full RAG pipeline: top-20 retrieved → cross-encoder → top-5 to LLM.

### Agent & Observability
- [ ] Every tool has a typed `args_schema` (Pydantic model) and a clear docstring.
- [ ] System prompt is in `api/prompts/system.md` — not in a string literal.
- [ ] Langfuse shows traces for every chatbot run (D16).
- [ ] All logs use `structlog` — zero `print()` statements in production code.

### Docker
- [ ] I can explain every line in every `Dockerfile` and in `docker-compose.yml`.
- [ ] Networks and volumes serve different purposes — I know which I use and why.
- [ ] `docker compose up` brings the entire stack up with one command.
