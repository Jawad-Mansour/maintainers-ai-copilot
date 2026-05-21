# Implementation Progress

Last updated: 2026-05-20

## Status Legend
- ✅ Done — code written, tests passing
- 🔧 Written — code written, tests not yet run
- ⏳ Pending — not started

---

## Phase Overview

| Phase | Name | Status | Notes |
|-------|------|--------|-------|
| 0 | Repo scaffold + tooling | ✅ Done | pyproject, ruff, mypy, pre-commit, directory structure |
| 0-T | Phase 0 tests | ✅ Done | Verified tooling passes |
| 1 | Infrastructure | ✅ Done | docker-compose (10 services), Vault, Alembic migrations, refuse-to-boot |
| 1-T | Phase 1 tests | ✅ Done | Boot guard (5), DB migrations (3), Redis TTL (4) — 12 tests total |
| 2 | API shell + Auth + Observability | ✅ Done | JWT auth, conversation CRUD, redaction, exception hierarchy, Langfuse |
| 2-T | Phase 2 tests | ✅ Done | 39/39 passing — auth, crud, crud_routes, redaction, exceptions |
| 3 | Advanced RAG pipeline | ✅ Done | Parent-child chunking, hybrid retrieval, HyDE, reranking, metadata filter, MinIO |
| 3-T | Phase 3 tests | ✅ Done | 20/20 passing — chunker, rag_service, routes |
| 4 | Chatbot pipeline + Memory | ✅ Done | Chat service, Redis history, semantic memory, Langfuse tracing, prompts/ dir |
| 4-T | Phase 4 bug fixes + tests | ✅ Done | 16/16 passing — chat_service, memory, routes; 87 total tests green |
| 5 | Widget backend + tool-calling (non-UI) | ✅ Done | Tool-calling loop, widget CRUD, SSE stream, CORS |
| 5-UI | Streamlit + React + host demo | ⏳ Pending | 5-C, 5-D, 5-E |
| 5-T | Phase 5 non-UI tests | ✅ Done | 19 new tests, 106 total passing |
| 6 | Evals + CI | ⏳ Pending | |
| 6-T | Phase 6 tests | ⏳ Pending | |
| 7 | ML/DL (training + real modelserver) | ✅ Done | 7-A training complete; artifacts in MinIO; modelserver boots in real mode |
| 7-T | Phase 7 tests | ✅ Done | 13/13 passing; modelserver confirmed mode=real; 119 total tests green |
| 8 | Docs + polish + tag | ⏳ Pending | |

---

## Detailed Breakdown

---

### ✅ Phase 0 — Repo scaffold + tooling
- pyproject.toml (ruff, mypy, pytest, pre-commit)
- Layered directory structure: api/app/{api,services,repositories,domain,infra}
- .gitignore, .env.example

---

### ✅ Phase 1 — Infrastructure
- docker-compose.yml: vault, db, redis, minio, migrate, api, modelserver, chatbot, widget, host
- vault-init one-shot job (seeds all secrets)
- minio-init one-shot job (creates buckets)
- Alembic migrations: users, conversations, messages, memories, chunks, widgets, audit_log
- eval_thresholds.yaml committed with all values > 0
- Refuse-to-boot: Vault unreachable → sys.exit(1), thresholds missing/zero → sys.exit(1)

---

### ✅ Phase 2 — API shell + Auth + Observability
**Auth:**
- POST /auth/register, POST /auth/login, GET /auth/me
- JWT HS256, signing key from Vault, 24h expiry
- passlib bcrypt password hashing
- Two roles: user, admin

**Conversations:**
- POST /conversations, GET /conversations, DELETE /conversations/{id}
- POST /conversations/{id}/messages, GET /conversations/{id}/messages
- Ownership check + PermissionDenied on wrong user

**Observability:**
- structlog + redacting processor (7 patterns: OpenAI keys, GitHub PATs, passwords, Bearer JWT, MinIO secret keys)
- Langfuse tracing wired (trace per chat turn, generation span per LLM call)
- Single exception handler: AppError → structured JSON with code + request_id

**Infra:**
- VaultSecrets dataclass (5 KV paths: postgres, openai, jwt, minio, langfuse)
- audit_log table + audit_repo.log() used for: conversation delete, memory write

---

### ✅ Phase 3 — Advanced RAG pipeline
**Chunking:**
- Parent-child: child 256 tokens (embedded), parent 1024 tokens (returned to LLM)
- tiktoken cl100k_base encoder
- source + label propagated to all chunks

**Retrieval:**
- pgvector HNSW index (m=16, ef_construction=64, cosine ops)
- PostgreSQL FTS (tsvector + GIN index, auto-populated by trigger)
- Hybrid search: 0.6 × dense + 0.4 × sparse, top-20 candidates
- Metadata filter: WHERE label + source before HNSW scan

**Query transformation:**
- HyDE: LLM generates hypothetical answer, blend 50/50 with original query vector

**Reranking:**
- ms-marco-MiniLM-L-6-v2 via modelserver: top-20 → top-5
- modelserver_client.rerank() wired in rag_service.search()

**MinIO:**
- Per-conversation chunk snapshot saved after every search

**Routes:**
- POST /rag/ingest (admin only)
- POST /rag/search (authenticated)

---

### ✅ Phase 4 — Chatbot pipeline + Memory
**Chat service:**
- Pipeline: ownership check → classify → memories → RAG → history → LLM → persist → Redis update
- Ownership check: conversation_repo.get() → PermissionDenied if wrong user
- Tool failure recovery: classify + RAG wrapped in try/except ToolFailure → fallback to label="unknown" / chunks=[]
- No auto-memory writes (explicit write_memory tool only — Phase 5)

**Memory:**
- Semantic long-term memory in pgvector (memories table)
- save_memory() → embed → insert → audit_repo.log(action="write_memory")
- get_relevant_memories() → embed query → cosine search top-3

**Redis short-term history:**
- Key: `conversation:{id}`, TTL 24h
- Cache hit: skip DB query
- After reply: append user+assistant turn, update cache

**Prompts:**
- api/prompts/system.md, hyde.md, summarize.md
- load_prompt() in api/app/infra/prompts.py (lru_cache)

**Modelserver:**
- POST /classify → mock {"label": "bug", "mode": "mock"}
- POST /rerank → mock decreasing scores
- POST /ner → mock {"entities": []}

**Phase 4-T bug fixes applied:**
1. Removed save_memory auto-call (D14 violation)
2. Added conversation ownership check
3. Wired reranker (final_k=20 → rerank → slice top-5)
4. Created api/prompts/ directory with all 3 templates
5. Wired Redis for conversation history
6. Added modelserver health check to refuse-to-boot
7. Added audit log to every memory write
8. Added graceful tool failure recovery (classify + RAG)

**All 106 tests passing (confirmed 2026-05-20 after Phase 5):**
- test_phase1_db.py (3 tests — testcontainers)
- test_phase2_auth.py (7), test_phase2_crud.py (7), test_phase2_crud_routes.py (6)
- test_phase2_redaction.py (9), test_phase2_exceptions.py (10)
- test_phase3_chunker.py (11), test_phase3_rag_service.py (5), test_phase3_routes.py (4)
- test_phase4_chat_service.py (6), test_phase4_memory.py (5), test_phase4_routes.py (5)

---

### ✅ Phase 5 (Non-UI) — Tool-calling agent + Widget backend

**5-A: Tool-calling LLM refactor (biggest change)**
- Define 5 OpenAI tool schemas in api/app/tools/:
  - classify_issue, search_knowledge_base, extract_entities, summarize_thread, write_memory
- Refactor chat_service into tool-calling loop (LLM picks tools, not hardcoded pipeline)
- Add POST /summarize stub to modelserver

**5-B: Widget backend**
- Widget CRUD routes (admin only): POST/GET/PUT/DELETE /widgets
- GET /widget.js — loader script (injects iframe with data-widget-id)
- POST /chat/stream — SSE streaming endpoint
- Dynamic CORS middleware from allowed_origins DB field
- Content-Security-Policy: frame-ancestors from allowed_origins

**5-C: Streamlit app (chatbot/)**
- Login page (email + password → JWT in session)
- Chat page (SSE streaming, shows label + sources)
- Memory inspector (list + delete memories)
- Widget config page (admin only: create/edit widgets, copy embed snippet)

**5-D: React widget (widget/)**
- Vite + React + Tailwind CSS
- Components: ChatBubble (collapsed), ChatPanel (expanded), MessageList, MessageInput
- EventSource SSE for streaming tokens
- postMessage to host on resize
- Load config from GET /widgets/{id} at mount, apply theme

**5-E: Host demo page (host/)**
- Single index.html with script tag embedding the widget
- nginx container serves it
- Demo: widget loads on allowed host, blocked on disallowed host (CSP)

**5-T: Tests**
- Widget CRUD routes
- SSE endpoint shape
- CORS allowlist enforcement
- Tool-calling loop (mock LLM returns tool_calls, verify tool execution)

---

### ⏳ Phase 6 — Evals + CI

**6-A: Eval scripts (I write)**
- evals/run_rag_eval.py — RAGAS: faithfulness, answer_relevancy, context_precision + hit@5 + MRR@10
- evals/run_classification_eval.py — macro-F1, per-class F1, confusion matrix for all 3 models
- Both write eval_report.json to MinIO, exit non-zero on regression

**6-B: CI workflow (I write)**
- .github/workflows/ci.yml
- Jobs: lint (ruff) → type-check (mypy) → pytest (mock tests) → build images → redaction test → both eval suites

**6-C: Golden sets (YOU curate — cannot be automated)**
- evals/golden_rag.json — 25 triples: {question, ideal_answer, ground_truth_chunks}
  - Questions a real maintainer would ask about pandas-dev/pandas issues
  - NOT from training split
- evals/golden_classification.json — 25 issues: {title, body, label}
  - Hand-verify each label
  - NOT from training split
- Hand-label 5 of the 25 RAG triples yourself (for RAGAS judge agreement check)

---

### ✅ Phase 7 — ML/DL

**7-A: Training notebook (written, YOU run on Colab T4)**
- notebooks/train_classifier.ipynb
- Fetch pandas-dev/pandas closed issues via GitHub API
- 7-step preprocessing pipeline
- Stratified 70/15/15 split (test = most recent in time)
- Classical ML baseline: TF-IDF + Logistic Regression
- LLM zero-shot baseline: GPT-4o-mini
- DistilBERT fine-tune: AdamW lr=2e-5, 3 epochs, inverse-frequency class weights, W&B
- Three-way comparison table
- Save weights to MinIO with model card (SHA-256 of training data + weights)
- YOU: copy actual metrics into DECISIONS.md D-deploy section

**✅ 7-B: Real modelserver**
- app/vault.py — Vault secrets fetcher (minio + openai)
- app/weights.py — MinIO download + SHA-256 verify; WeightsNotFound → mock, mismatch → exit(1)
- app/classifier.py — DistilBERT inference (from /tmp/weights/distilbert_weights/)
- app/classical.py — TF-IDF + LR inference (from /tmp/weights/*.pkl)
- app/reranker.py — ms-marco-MiniLM-L-6-v2 cross-encoder
- app/ner.py — spaCy en_core_web_sm + EntityRuler (VERSION, EXCEPTION, PACKAGE)
- app/summarizer.py — GPT-4o-mini summarizer
- main.py — FastAPI lifespan: mock fallback if no weights, real mode after training
- /classify accepts {"texts": list[str]} → {"labels": list[str], "confidences": list[float], "mode": str}
- /classify/classical accepts {"text": str} → {"label", "confidence", "mode"} (eval only)

**✅ 7-C: API refuse-to-boot update**
- Verify modelserver /health returns mode="real" (not "mock")
- Controlled by REQUIRE_REAL_MODELSERVER env var (default false — mock mode still boots)

**7-T: Integration pass**
- docker-compose up with real weights
- Ingest real pandas corpus
- Run full chat turn end-to-end
- Verify Langfuse traces show real classifier scores

---

### ⏳ Phase 8 — Docs + polish + tag

- ARCH.md: ASCII diagram + data flow for a single chat turn
- RUNBOOK.md: fresh clone → docker-compose up → first request, ops procedures
- EVALS.md: methodology, golden set construction, RAGAS agreement with 5 hand-labeled, actual metric numbers
- SECURITY.md: justify all 7 redaction patterns, trace a GitHub PAT through every surface it could hit
- DECISIONS.md: fill D-deploy with actual F1 numbers from Phase 7 training run
- Verify docker-compose up from fresh clone passes all health checks
- git tag v0.1.0-week7 + push

---

## Critical path

```
4-T tests pass
    → 5-A tool-calling LLM
    → 5-B widget backend
    → 5-C Streamlit
    → 5-D React widget (YOU: npm run build)
    → 5-E host demo
    → 6-A eval scripts
    → 6-B CI
    → 6-C golden sets (YOU: manual curation) ← parallel with 5
    → 7-A notebook (YOU: run on Colab) ← can start parallel with 5
    → 7-B real modelserver
    → 7-C API update
    → 8 docs + tag
```

## What YOU must do manually

| Task | Phase | Blocker for |
|------|-------|-------------|
| npm run build (React widget) | 5-D | Host demo |
| Curate evals/golden_rag.json (25 triples) | 6-C | Eval scripts |
| Curate evals/golden_classification.json (25 issues) | 6-C | Eval scripts |
| Hand-label 5 RAG triples for RAGAS agreement | 6-C | EVALS.md |
| Run notebooks/train_classifier.ipynb on Colab T4 | 7-A | Real modelserver |
| Copy training metrics into DECISIONS.md D-deploy | 7-A | Docs complete |
| git add + commit after each phase | all | Phase gates |
| git tag v0.1.0-week7 + push | 8 | Submission |
