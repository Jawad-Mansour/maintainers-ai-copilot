# Phase 0 — GitHub Repo + Tooling Setup

## Goal
Empty repo exists, all tooling configured, any developer can clone and run pre-commit successfully.

## Why This Phase Exists
Without a clean repo foundation — enforced linting, secret scanning, and type checking from day one — technical debt accumulates silently. A failing pre-commit hook on a real secret or a type error in production code is the kind of thing that causes demo failures.

## Deliverables

| File | Purpose |
|------|---------|
| `.gitignore` | Excludes `.env`, venvs, ML artifacts, secrets, IDE files |
| `.env.example` | Documents Vault bootstrap vars only — no real secrets |
| `pyproject.toml` | Ruff + Mypy config, dev dependency group |
| `.pre-commit-config.yaml` | Ruff, Mypy, Gitleaks, standard hooks |
| `requirements-dev.txt` | Frozen dev tool versions (uv pip freeze) |
| `api/requirements.txt` | Planned API service dependencies |
| `chatbot/requirements.txt` | Planned Streamlit admin dependencies |
| `modelserver/requirements.txt` | Planned ML inference dependencies |
| `resources/PROGRESS.md` | Phase gate tracking log |

## Directory Scaffold

```
maintainers-ai-copilot/
├── api/
│   ├── app/
│   │   ├── api/routes/     ← HTTP routing ONLY (no DB, no Redis)
│   │   ├── services/       ← Business logic + transactions
│   │   ├── repositories/   ← SQL queries only
│   │   ├── domain/         ← Pydantic domain models (NOT SQLAlchemy)
│   │   ├── infra/          ← Vault, MinIO, Redis, LLM, redaction adapters
│   │   ├── tools/          ← LLM tool definitions (function-calling)
│   │   └── prompts/        ← Prompt template files
│   ├── tests/
│   ├── main.py             ← FastAPI app entry
│   ├── config.py           ← pydantic-settings Settings class
│   ├── dependencies.py     ← FastAPI DI (get_db, get_current_user, etc.)
│   ├── requirements.txt
│   └── Dockerfile
├── chatbot/                ← Streamlit admin UI
├── widget/                 ← React embeddable widget (Vite + Tailwind)
├── modelserver/            ← DistilBERT inference + spaCy NER
├── host/                   ← Static HTML demo host page
├── db/migrations/          ← Alembic versions
├── evals/                  ← RAGAS evaluation scripts
├── notebooks/              ← Google Colab training notebooks
└── tests/                  ← Root integration tests (testcontainers)
```

## Tooling Configuration

### Ruff
- `line-length = 100`
- `select = ["E", "F", "I", "UP", "B", "SIM"]`
- `target-version = "py312"`

### Mypy
- `strict = true`
- `ignore_missing_imports = true`
- Excludes: `notebooks/`, `.specify/`, `.claude/`

### Pre-commit Hooks (in order)
1. `ruff` — lint + auto-fix
2. `ruff-format` — formatting
3. `mypy` — type check
4. `gitleaks` — secret scanning (blocks commit if any secret found)
5. Standard hooks: trailing whitespace, end-of-file, check-yaml/json, detect-private-key

## Acceptance Criteria (Phase 0-T)
- [ ] `uv run ruff check .` exits 0
- [ ] `uv run mypy .` exits 0
- [ ] `uv run pre-commit run --all-files` exits 0
- [ ] `.env` is NOT tracked by git
- [ ] `gitleaks` finds zero secrets in repository
