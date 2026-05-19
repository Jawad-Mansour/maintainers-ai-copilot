<!-- Sync Impact Report
Version change: 0.0.0 → 1.0.0
Added sections: All (initial population from project MD files)
Source files: resources/understanding.md, resources/DECISIONS.md, resources/GUIDELINES.md, PLAN.md
Deferred placeholders: None
-->

# Maintainer's AI Copilot — Constitution

## Core Principles

### I. Strictly Layered Architecture (NON-NEGOTIABLE)
Every request flows strictly downward through layers — no layer skips another:
- `app/api/routes/` → HTTP routing ONLY. No DB, no Redis, no external calls.
- `app/services/` → Business logic, transactions, cache invalidation.
- `app/repositories/` → SQL queries only. No HTTP errors, no cache logic.
- `app/domain/` → Pydantic domain models. NOT SQLAlchemy ORM models.
- `app/infra/` → Adapters for Vault, MinIO, Redis, LLM, modelserver, tracing, redaction.

Graders will add a new endpoint live on Friday. If a route touches SQLAlchemy directly — the demo fails.

### II. Vault for All Secrets (NON-NEGOTIABLE)
`.env` holds ONLY: `VAULT_ADDR`, `VAULT_TOKEN`, and service ports. Nothing else.
- ALL secrets (OpenAI key, DB password, JWT signing key, MinIO credentials, Langfuse keys) are fetched from Vault at startup via `hvac` client.
- App MUST refuse to boot if Vault is unreachable.
- `grep -ri 'sk-' app/` and `grep -ri 'password' app/` MUST return zero matches outside Vault-reading code.
- `.env` is in `.gitignore` always. `.env.example` with placeholder values is committed.

### III. Refuse to Boot on Any Critical Failure (NON-NEGOTIABLE)
The `api` container must exit non-zero (refuse to start) if ANY of these are true:
- Vault is unreachable or token invalid
- Classifier weights missing from MinIO
- Weights SHA-256 does not match model card
- Langfuse tracing credentials invalid
- Any committed eval threshold is zero or disabled

Not a warning — a hard exit. Prevents silent broken state.

### IV. Test-First, Phase-Gate (NON-NEGOTIABLE)
Do NOT move to the next phase until the current phase's test suite passes.
- Unit tests: pytest + pytest-asyncio (no containers, fast)
- Integration tests: testcontainers (real postgres, redis)
- Mock external calls: respx (HTTP), factory_boy (fixtures)
- E2E: docker-compose + httpx scripts (Phase 8 only)
- Coverage target: 80% on new code, 95% on auth and data mutation paths.
- A test that does not run in CI does not exist.

### V. Redact Before Any External Write
Before any log line, trace span, or long-term memory write — the redaction layer in `app/infra/redact.py` MUST run:
- `sk-[a-zA-Z0-9]{48}` → `[REDACTED_OPENAI_KEY]`
- `ghp_[a-zA-Z0-9]{36}` → `[REDACTED_GITHUB_TOKEN]`
- `password=\S+` → `[REDACTED_PASSWORD]`
Explicitly tested: a test asserts a message containing a fake API key never appears unredacted in logs, traces, or memory.

### VI. Async All the Way Down
Every I/O operation MUST be async. One blocking call freezes the entire event loop.
- Use `httpx.AsyncClient` for all HTTP. Never `requests` in a request path.
- Use `AsyncOpenAI` client.
- Use SQLAlchemy 2.x async mode for all DB queries.
- Use `asyncio.gather()` for parallel I/O (dense + sparse retrieval run in parallel).
- Use `asyncio.to_thread()` for CPU-bound work (DistilBERT inference, spaCy NER, cross-encoder).
- Never `time.sleep()` in async code.

### VII. Every Decision Backed by a Number
No choice without justification in `resources/DECISIONS.md`.
- 18+ decisions already documented with concrete tradeoffs and numbers.
- D-deploy table MUST be filled with actual training metrics before Friday demo.
- "The AI suggested it" is not a valid answer to any grader question.

## Architecture Decisions (Summary)

All 18+ decisions are documented in full in `resources/DECISIONS.md`. Key choices:

| Area | Choice | Reason |
|------|--------|--------|
| Dataset | pandas-dev/pandas (14,869 issues) | Only repo with all 4 classes; 1,656 question samples |
| ML classifier | TF-IDF + Logistic Regression | Classical baseline (required by assignment) |
| DL classifier | distilbert-base-uncased, full fine-tune | Small encoder, 8min/epoch Colab T4, ~0.87–0.91 F1 |
| LLM | GPT-4o-mini | Best tool-calling at $0.15/1M tokens |
| Embeddings | text-embedding-3-small | MTEB 62.3, $0.05 total corpus cost |
| Chunking | Hierarchical parent (1024) / child (256) | Sharp embeddings + rich LLM context |
| Vector store | pgvector + HNSW | Already in stack, same algo as Qdrant |
| Sparse retrieval | PostgreSQL FTS (tsvector + GIN) | Zero new infra, single hybrid SQL query |
| Reranker | ms-marco-MiniLM-L-6-v2 | Top-20 → top-5, 200ms CPU |
| Query transform | HyDE (50/50 blend) | Bridges question-shaped queries to answer-shaped corpus |
| NER | spaCy + EntityRuler | Deterministic regex, 12MB vs 400MB HuggingFace NER |
| Summarizer | LLM call | Zero modelserver memory, domain-aware, versioned prompt |
| Long-term memory | Semantic (pgvector) | Stable facts reusable across sessions |
| TTL | 24h conversation / 5min cache | Full working day; overnight clears stale context |
| Tracing | Langfuse cloud | LLM-native spans, zero extra container |
| RAG eval | RAGAS | Purpose-built metrics, one function call in CI |
| Widget CSS | Tailwind + Vite | PurgeCSS → 3–5KB, 3–4x faster to build |
| Experiment tracking | Weights & Biases | Zero infra, native Colab, best demo UI |

## Technology Stack

**Backend:** FastAPI, pydantic-settings (`extra="forbid"`), fastapi-users (JWT, 2 roles), SQLAlchemy 2.x async, Alembic, structlog, tenacity, hvac

**ML/DL:** distilbert-base-uncased (HuggingFace), spaCy en_core_web_sm + EntityRuler, cross-encoder/ms-marco-MiniLM-L-6-v2, scikit-learn (TF-IDF + LR), Weights & Biases, Google Colab T4

**RAG:** text-embedding-3-small, pgvector + HNSW, PostgreSQL FTS, RAGAS

**Infrastructure:** Docker Compose (10 services), HashiCorp Vault, PostgreSQL 16 + pgvector, Redis 7, MinIO, Langfuse cloud

**Frontend:** Streamlit (admin), React + Vite + Tailwind CSS (embeddable widget)

**Testing:** pytest, pytest-asyncio, respx, factory_boy, testcontainers

**10 Docker services:** `api`, `chatbot`, `widget`, `modelserver`, `host`, `migrate`, `db`, `redis`, `minio`, `vault`

## Development Workflow

**Phase gate rule:** Tests for Phase N must pass before Phase N+1 begins. No exceptions.

**Phases (ML/DL is last):**
0 → 0-T → 1 → 1-T → 2 → 2-T → 3 → 3-T → 4 → 4-T → 5 → 5-T → 6 → 6-T → 7 → 7-T → 8 → 8-T

**Git conventions:**
- Branch naming: `feature/<desc>`, `bugfix/<desc>`, `chore/<desc>`
- Commit format: `feat(rag): add hybrid retrieval` (Conventional Commits, imperative, <72 chars)
- Never commit directly to `main`
- PRs: <400 lines, one concern per PR

**Code quality (enforced in CI):**
- `ruff check . && ruff format .` — zero errors
- `mypy .` strict mode — zero errors
- `gitleaks` — zero secrets detected
- All function signatures have type hints

**Required docs (must exist before tag v0.1.0-week7):**
`ARCH.md`, `DECISIONS.md`, `RUNBOOK.md`, `EVALS.md`, `SECURITY.md`, `README.md`

## Submission Requirements

**Tag:** `v0.1.0-week7` on public GitHub repo.
**Must work:** `docker-compose up` from fresh clone after `cp .env.example .env`.
**Deadline:** Thursday May 21, 2026 @ 12:00 PM.

**Submission block must include:**
- Dataset: pandas-dev/pandas, N train / N val / N test
- Classification F1: classical / fine-tuned / LLM (fill after Phase 7)
- Deployment choice + one-line reason
- Embedding model + one-line reason
- RAG metrics: hit@5, MRR@10, faithfulness, answer relevancy
- Long-term memory type: semantic
- Tracing backend: Langfuse
- Widget bundle size (gzipped KB)
- LLM: GPT-4o-mini

## Governance

This constitution supersedes all other practices. Any deviation requires updating this file with rationale.

- All code MUST comply with GUIDELINES.md (synthesized from 5 guideline sources)
- All decisions MUST be documented in DECISIONS.md with a number or concrete tradeoff
- Pre-demo checklist in GUIDELINES.md Section 19 MUST pass before Friday
- Grader questions are pre-answered in DECISIONS.md and SECURITY.md — not improvised on demo day

**Version**: 1.0.0 | **Ratified**: 2026-05-20 | **Last Amended**: 2026-05-20
