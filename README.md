# Maintainer's AI Copilot

An authenticated AI chatbot for open-source maintainers to triage GitHub issues.

## What It Does

- Classifies GitHub issues into: **bug / feature / docs / question**
- Answers maintainer questions using a RAG pipeline over the issue history
- Remembers context across sessions (semantic long-term memory)
- Embeddable as a widget on any project page

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + SQLAlchemy 2.x async + Alembic |
| Auth | fastapi-users (JWT, roles: user / admin) |
| LLM | GPT-4o-mini (tool-calling) |
| Embeddings | text-embedding-3-small |
| Vector store | pgvector + HNSW |
| Sparse retrieval | PostgreSQL FTS (tsvector + GIN) |
| Reranker | ms-marco-MiniLM-L-6-v2 |
| ML classifier | TF-IDF + Logistic Regression |
| DL classifier | distilbert-base-uncased (fine-tuned) |
| NER | spaCy + EntityRuler |
| Cache / memory | Redis 7 (24h TTL conversation, 5min API cache) |
| Object store | MinIO (model weights, eval reports) |
| Secrets | HashiCorp Vault (all secrets — `.env` holds only bootstrap vars) |
| Tracing | Langfuse |
| Evaluation | RAGAS |
| Frontend | React + Vite + Tailwind CSS (embeddable widget) |
| Admin UI | Streamlit |

## Quick Start

```bash
cp .env.example .env          # Only VAULT_ADDR, VAULT_TOKEN, ports — no real secrets
docker-compose up
```

## Development

```bash
uv sync --group dev           # Install dev tools
uv run pre-commit install     # Install git hooks
uv run pytest                 # Run tests
```

## Architecture

Requests flow strictly downward through layers — no layer skips another:

```
app/api/routes/     ← HTTP routing only
app/services/       ← Business logic + transactions
app/repositories/   ← SQL queries only
app/domain/         ← Pydantic domain models
app/infra/          ← Vault, Redis, MinIO, LLM, redaction adapters
```

## Documentation

| Doc | Contents |
|---|---|
| [ARCH.md](ARCH.md) | System architecture, service map, request flow, RAG pipeline |
| [DECISIONS.md](resources/DECISIONS.md) | All 18 technical decisions with justifications and tradeoffs |
| [RUNBOOK.md](RUNBOOK.md) | First boot, service URLs, common ops, debugging guide |
| [EVALS.md](EVALS.md) | Evaluation methodology, golden sets, thresholds, measured results |
| [SECURITY.md](SECURITY.md) | Redaction patterns with pattern-by-pattern justification |

## Submission

Tag: `v0.1.0-week7` | Deadline: Thursday May 21, 2026 @ 12:00 PM
