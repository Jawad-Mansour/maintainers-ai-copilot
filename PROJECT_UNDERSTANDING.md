# Maintainer's Copilot — Full System Understanding

> Reference file. Every part of the system: what it is, prerequisites, workflow, output, connections.

---

## THE PRODUCT (What we're building)

An authenticated chatbot for open-source maintainers triaging GitHub issues. It:
- Classifies issues (bug/feature/docs/question) using 3 compared models
- Extracts entities (NER) and summarizes threads
- Answers questions via advanced RAG over docs + resolved issues
- Carries memory across conversations
- Is embeddable as a standalone React widget in any host app
- Has golden evals that fail CI when they regress

---

## THE DATA PIPELINE (Foundation of everything)

### Source
- `data/pandas_closed_issues.jsonl` — raw closed GitHub issues from pandas repo
- Chosen repo: **pandas**. Label mapping defined in DECISIONS.md

### Flow
```
pandas_closed_issues.jsonl
        ↓ scripts/process_issues.py
        ↓ preprocessing: clean text, map labels → bug/feature/docs/question
        ↓ stratified split (test = strictly more recent in time than train)
        ├── data/train.jsonl   → classifier training
        ├── data/val.jsonl     → classifier validation
        ├── data/test.jsonl    → classifier evaluation (all 3 models same split)
        └── data/rag_corpus.jsonl → held-out resolved issues + docs (NEVER in classifier training)
```

### Outputs used by
- `train/val/test.jsonl` → model training (notebooks, modelserver)
- `rag_corpus.jsonl` → RAG ingestion pipeline (scripts/bulk_ingest_corpus.py → api pgvector)
- `data/processing_report.json` → documents preprocessing decisions

### Golden Sets (hand-curated, separate from all splits)
- `evals/golden_classification.json` — 25 issues with correct labels
- `evals/golden_rag.json` — 25 question/ideal-answer/ground-truth-chunks triples
- 5 of the 25 RAG triples hand-labeled by human; rest by judge model. Agreement reported.

---

## PILLAR 1 — DEEP LEARNING TRACK

### What it produces: the Model Server

#### A. Fine-tuned Transformer (DistilBERT)
- **Input**: train.jsonl
- **Process**: `notebooks/train_classifier.ipynb` — fine-tune DistilBERT for 4-class classification
- **Tracked with**: real run logger (MLflow/W&B)
- **Output**: `data/maintainers-copilot-artifacts/distilbert_weights/` + `model_card.json`
- **Model card contains**: architecture, hyperparameters, training data hash, final metrics
- **Freeze policy**: documented — which layers frozen and why

#### B. Classical ML Baseline (Logistic Regression)
- **Input**: same train/test splits
- **Process**: TF-IDF vectorizer + LogReg/SVM
- **Output**: `data/maintainers-copilot-artifacts/lr_model.pkl` + `tfidf_vectorizer.pkl`

#### C. LLM Baseline
- **Input**: same test split only (zero/few-shot, no training)
- **Output**: `data/maintainers-copilot-artifacts/llm_predictions.json`

#### Three-way comparison (in DECISIONS.md)
| Model | Accuracy | Macro-F1 | Per-class F1 | Latency | Cost |
|-------|----------|----------|--------------|---------|------|
| Classical ML | n | n | n | n | n |
| Fine-tuned XFMR | n | n | n | n | n |
| LLM baseline | n | n | n | n | n |
→ Deployment choice defended with numbers

#### D. NLP Pipelines (NER + Summarizer)
- **NER**: extracts code-shaped entities (package names, error types, function names) from issue text
- **Summarizer**: pre-trained or LLM-driven, condenses issue threads
- Both exposed as FastAPI endpoints in the model server

### The Model Server (`modelserver/`)
```
modelserver/
├── main.py              ← FastAPI app
└── app/
    ├── classifier.py    ← fine-tuned XFMR inference
    ├── classical.py     ← LR inference
    ├── ner.py           ← NER pipeline
    ├── summarizer.py    ← summarization pipeline
    ├── reranker.py      ← cross-encoder reranking (used by RAG)
    ├── weights.py       ← loads weights from MinIO, verifies SHA-256
    └── vault.py         ← reads secrets from Vault at startup
```

**Boot sequence**: loads weights from MinIO → verifies SHA-256 vs model card → refuses to start if mismatch

**Endpoints**:
- `POST /classify` → {label, confidence}
- `POST /ner` → {entities: [...]}
- `POST /summarize` → {summary}
- `POST /rerank` → {reranked_chunks: [...]}

**Who calls it**: the FastAPI `api` service, via HTTP, from tool functions

---

## PILLAR 2 — ADVANCED RAG

### Prerequisites
- `data/rag_corpus.jsonl` (held-out resolved issues + docs)
- Embedding model chosen (justified by retrieval quality number vs alternative)
- pgvector running in Postgres

### Ingestion Flow
```
rag_corpus.jsonl
    ↓ scripts/bulk_ingest_corpus.py
    ↓ smart chunking (NOT naive fixed-size — e.g. semantic/sentence-based)
    ↓ embed each chunk (chosen embedding model)
    ↓ store in Postgres chunks table (text + embedding + metadata)
    → also index BM25 sparse for hybrid retrieval
```

### Query Flow (called at chat time)
```
User question
    ↓ query transformation (HyDE: generate hypothetical answer, embed that)
    ↓ hybrid search:
    │   ├── dense:  pgvector cosine similarity
    │   └── sparse: BM25 keyword match
    │   └── weighted combination (tuned weight α)
    ↓ metadata filtering (e.g. by issue type, date)
    ↓ cross-encoder reranker (modelserver /rerank) on top-k
    ↓ top chunks returned to LLM as context
    ↓ LLM generates answer grounded in chunks
    ↓ retrieved chunks snapshot saved to MinIO (last N conversations)
```

### RAG Infrastructure in API
```
api/app/
├── services/rag_service.py      ← orchestrates full retrieval pipeline
├── repositories/chunk_repo.py   ← SQL: vector search, BM25 search
└── infra/embedder.py            ← wraps embedding model calls
```

### Prompts
- `api/prompts/hyde.md` — HyDE query transformation prompt
- `api/prompts/system.md` — main system prompt
- `api/prompts/summarize.md` — summarization prompt

---

## PILLAR 3 — THE CHATBOT

### Architecture: ONE tool-calling LLM (not multi-agent, not workflow)
The LLM receives the conversation + available tools and PICKS which to call.

### Authentication
```
fastapi-users library + JWT
JWT signing key ← resolved from Vault at startup (never hardcoded)
Two roles:
  - user  → can chat, use widget
  - admin → can invite users, configure widgets, access Streamlit admin
```

### Tools the LLM can call
| Tool | Calls | Returns |
|------|-------|---------|
| `classify_issue` | modelserver /classify | label + confidence |
| `extract_entities` | modelserver /ner | entities list |
| `summarize` | modelserver /summarize | summary text |
| `rag_search` | internal RAG pipeline | top chunks + answer |
| `write_memory` | postgres memory table | confirmation (explicit only, no auto-writes) |

Prompts stored in `api/prompts/` — version controlled, NOT hardcoded in code.

### Memory
```
Short-term (Redis, redis:7):
  - conversation state (message history for current session)
  - TTL: explicit and justified (e.g. 2 hours of inactivity)
  - key: session_id → serialized messages

Long-term (Postgres pgvector):
  - episodic memory: "user asked about X last week"
  - stored as embeddings for semantic recall
  - EVERY write → audit_log row: {actor, action, target, timestamp}
  - recalled at conversation start to prime context
```

### Chat Request Flow
```
POST /chat (JWT Bearer token)
    ↓ validate JWT → get user_id
    ↓ load short-term context from Redis (session_id)
    ↓ recall relevant long-term memories from pgvector
    ↓ build prompt: system + memories + history + user message
    ↓ LLM called → may pick tools (loop until no more tool calls)
    │   each tool call:
    │   ├── redact inputs
    │   ├── call tool (HTTP to modelserver or internal)
    │   ├── redact outputs
    │   └── add result to context
    ↓ final LLM response
    ↓ save updated history to Redis
    ↓ stream response to client
    ↓ retrieved chunks snapshot → MinIO
```

---

## THE TWO FRONTENDS

### Frontend 1: Streamlit (`chatbot/main.py`) — Internal Tool
**Who uses it**: maintainers (authenticated), admins
**Pages**:
- Login / registration
- Full chat interface
- Memory inspector (view long-term memories for a user)
- Admin: create/edit widget configs, see embed snippet

**Calls**: FastAPI backend over HTTP (same API as widget)

### Frontend 2: React Widget (`widget/`) — Production Surface
**Who uses it**: any host app that embeds it
**What it looks like**:
- Collapsed bubble (bottom-right corner) → click to expand
- Chat panel + input box + streamed messages
- Theme (primary color, position) loaded from API at runtime, not hardcoded

**Tech**: Vite → single bundled JS file, served from static server or MinIO

### Embed Flow
```
Host page:
<script src="https://api/widget.js" data-widget-id="abc123"></script>
        ↓ loader script runs
        ↓ injects <iframe src="https://widget-server/index.html?widget_id=abc123">
        ↓ React widget boots
        ↓ GET /api/widget/abc123 → loads {theme, greeting, enabled_tools, allowed_origins}
        ↓ widget styles itself, opens chat
        ↓ postMessage channel with host (minimum: iframe resize events)

CORS: enforced from DB allowed_origins field (NOT hardcoded env var)
CSP:  embed route sets frame-ancestors = allowed_origins
      → unallowed parent → browser blocks the iframe
```

### Widget Config (in Postgres)
```
widget table:
  - widget_id (public)
  - allowed_origins (list of URLs)
  - theme (primary_color, position)
  - greeting (text)
  - enabled_tools (which tools this widget can call)
```

---

## EVALUATION (CI Gates)

### Classification Eval
- **Input**: `evals/golden_classification.json` (25 hand-curated examples, separate from test split)
- **Script**: `evals/run_classification_eval.py`
- **Metrics**: macro-F1, per-class F1, confusion matrix — run against ALL 3 models
- **Threshold**: committed in `eval_thresholds.yaml`

### RAG Eval
- **Input**: `evals/golden_rag.json` (25 Q/ideal-answer/ground-truth-chunks triples)
- **Script**: `evals/run_rag_eval.py`
- **Metrics**: hit@5, MRR@10, faithfulness, answer relevancy (RAGAS or frozen judge)
- **Human agreement**: 5 of 25 hand-labeled, report agreement with judge
- **Threshold**: committed in `eval_thresholds.yaml`

### CI Behavior (every push)
```
lint + type-check
→ build all images
→ run classification eval → write eval_report.json → upload to MinIO
→ diff vs last green build → below threshold → BLOCK merge
→ run RAG eval → same
→ run redaction test (fake API key never appears in logs/traces/memory)
→ smoke-test full stack
```

---

## OBSERVABILITY (wraps every service)

### Tracing (Langfuse)
```
User message arrives → root span created (trace_id)
    ├── LLM call span: model, tokens, latency, inputs/outputs (after redaction)
    ├── tool call span: tool name, inputs/outputs (after redaction)
    └── RAG retrieval span: query, chunks, scores, latency

trace_id logged alongside EVERY structured log line
→ logs + traces are joinable in Langfuse UI

Friday demo: walk a real trace tree including one error path
```

### Redaction (app/infra/redaction.py)
```
Runs BEFORE any log line, trace span, or memory write leaves service boundary
Patterns: API keys (sk-xxx), tokens, emails, PII
Tested: assert fake_api_key NOT IN logs/traces/memory
Applied by every service (api, modelserver, chatbot)
Defended in SECURITY.md
```

### Exception Handling
```
Domain exceptions: NotFoundError, PermissionDenied, ToolFailure, ...
    ↓ single exception handler at API boundary
    ↓ maps to HTTP {code, message, request_id} — no stack traces to user

Tool failure recovery:
    classifier down → chatbot catches ToolFailure → tells user → falls back
    never propagates as 500

Every uncaught exception → logged with trace_id + request_id
```

---

## ENGINEERING LAYERS (enforced, graded on Friday)

```
api/app/
├── api/          ← HTTP only. Routers touch NOTHING: no SQLAlchemy, no Redis, no external
├── services/     ← business logic, transaction boundaries, cache invalidation
├── repositories/ ← SQL only. No HTTP errors. No cache ops.
├── domain/       ← Pydantic models (SEPARATE from SQLAlchemy ORM models)
└── infra/        ← adapters: Vault, MinIO, Redis, LLM, modelserver, tracing, redaction
```

**The boundary is live-tested on Friday**: examiner asks you to add a new endpoint or tool.

---

## SECRETS & INFRASTRUCTURE

### Secrets (Vault)
```
.env contains ONLY: Vault root token + service ports
Vault contains: LLM API key, JWT signing key, DB password, MinIO credentials, tracing key

grep -ri 'sk-' app/ → 0 matches outside Vault-reading code
grep -ri 'password' app/ → 0 matches outside Vault-reading code
```

### Blob Storage (MinIO)
```
Holds:
- model artifacts (distilbert_weights/) or manifest pointing to them
- eval_report.json from every CI run
- training plots (confusion matrices, training curves)
- per-conversation retrieved-chunks snapshots (last N conversations)
```

### Database (Postgres 16 + pgvector)
```
Tables (via Alembic migrations):
- users           (fastapi-users managed)
- chunks          (RAG corpus: text, embedding vector, metadata)
- conversations   (session metadata)
- memories        (long-term memory: text, embedding, type)
- audit_log       (actor, action, target, timestamp — every sensitive write)
- widgets         (widget_id, allowed_origins, theme, greeting, enabled_tools)

migrate container: runs `alembic upgrade head` → exits → then api boots
```

### Refuse to Boot (api startup checks)
```
api refuses to start if ANY of:
  ✗ Vault unreachable
  ✗ Classifier weights missing from MinIO
  ✗ Weights SHA-256 ≠ model card SHA-256
  ✗ Tracing backend misconfigured
  ✗ Any eval threshold = 0 or disabled in eval_thresholds.yaml
```

---

## COMPOSE STACK (all services)

| Service | Image | Role |
|---------|-------|------|
| `api` | FastAPI | auth, chat, memory, RAG, widget config |
| `chatbot` | Streamlit | admin UI, memory inspector, full chat |
| `widget` | nginx static | serves React bundle + /widget.js loader |
| `modelserver` | FastAPI | classify, NER, summarize, rerank |
| `host` | nginx | demo host app (embeds the widget) |
| `migrate` | Python | runs alembic upgrade head, then exits |
| `db` | postgres:16+pgvector | all persistent data |
| `redis` | redis:7 | short-term memory, cache |
| `minio` | minio/minio | blob storage |
| `vault` | hashicorp/vault | secrets |

**Startup order**: vault + db + redis + minio → migrate → api + modelserver → chatbot + widget + host

---

## FULL END-TO-END FLOW (one user query)

```
[Host app / Maintainer]
        ↓ widget embed OR Streamlit login
        ↓ JWT token obtained

POST /api/chat {message: "Is #1234 a bug or feature request?"}
        ↓ JWT validated → user_id extracted
        ↓ Redis: load session history (short-term context)
        ↓ pgvector: recall relevant long-term memories
        ↓ build prompt (system.md + memories + history + message)
        ↓ LLM (Claude/GPT) called with tools available

        LLM decides: call classify_issue({text: issue_body})
        ↓ api → HTTP POST modelserver/classify
        ↓ modelserver: DistilBERT inference → {label: "bug", confidence: 0.94}
        ↓ redact inputs/outputs before trace span
        ↓ tool result added to context

        LLM decides: call rag_search({query: "how to handle this type of bug"})
        ↓ api → rag_service.search()
        ↓ HyDE: generate hypothetical answer → embed
        ↓ hybrid search (pgvector dense + BM25 sparse, α-weighted)
        ↓ metadata filter applied
        ↓ top-k → HTTP POST modelserver/rerank → reranked chunks
        ↓ chunks added to context

        LLM generates final response
        ↓ response streamed to client (SSE)
        ↓ Redis: save updated history
        ↓ MinIO: save retrieved chunks snapshot
        ↓ Langfuse: close trace tree

[Client receives streamed answer]
```

---

## PROJECT FILE MAP

```
maintainers-ai-copilot/
├── api/                        ← main FastAPI backend
│   ├── app/
│   │   ├── api/               ← HTTP routers only
│   │   ├── services/          ← business logic
│   │   ├── repositories/      ← SQL access
│   │   ├── domain/            ← Pydantic models
│   │   ├── infra/             ← adapters (Vault, MinIO, Redis, LLM, tracing, redaction)
│   │   ├── tools/             ← tool functions the LLM can call
│   │   └── prompts/           ← system.md, hyde.md, summarize.md
│   ├── alembic/               ← DB migrations
│   └── tests/                 ← unit + integration tests per phase
├── chatbot/main.py             ← Streamlit frontend
├── widget/                     ← React widget (Vite build → single JS bundle)
├── modelserver/                ← FastAPI inference: classify, NER, summarize, rerank
├── host/index.html             ← demo host app embedding the widget
├── evals/                      ← golden sets + eval scripts
│   ├── golden_classification.json
│   ├── golden_rag.json
│   ├── run_classification_eval.py
│   └── run_rag_eval.py
├── data/                       ← processed datasets + model artifacts
├── scripts/                    ← data pipeline scripts
├── db/                         ← init scripts for Postgres, MinIO, Vault
├── notebooks/                  ← classifier training notebook
├── eval_thresholds.yaml        ← committed CI thresholds
├── eval_classification_report.json  ← written by eval, stored in MinIO
├── eval_rag_report.json
├── docker-compose.yml
├── ARCH.md                     ← architecture doc
├── DECISIONS.md                ← every choice backed by a number
├── SECURITY.md                 ← redaction patterns defended
├── EVALS.md                    ← eval methodology
└── RUNBOOK.md                  ← how to run everything
```

---

## GRADING PRIORITIES (what the assignment weights most)

1. **Architecture is clean** — layers respected, graded live on Friday
2. **Evals work and fail CI** — committed thresholds mean something
3. **Every decision backed by a number** — DECISIONS.md is evidence
4. **Secrets in Vault, logs redacted, traces real** — proved by tests and demo
5. **No vibe coding** — every line explainable on Friday

---

## FINAL PRODUCT — WHAT IT LOOKS AND BEHAVES LIKE

### From a Maintainer's perspective (Streamlit)
```
1. Open Streamlit → login with email + password
2. See chat interface
3. Paste a GitHub issue URL or text → ask "Is this a bug?"
4. LLM responds:
   - "This is a BUG (confidence 0.94) — I classified it using DistilBERT"
   - "Entities found: DataFrame.merge(), KeyError, pandas 1.3.0"
   - "Summary: User reports merge() raises KeyError on duplicate column names"
   - "Similar resolved issues: [retrieved via RAG with links]"
5. Ask follow-up → bot remembers context (Redis short-term)
6. Next day: open new session → bot recalls "last week you asked about merge bugs" (pgvector long-term)
7. Admin tab → create widget config → copy embed snippet
8. Memory inspector → view/delete stored memories for any user
```

### From a Host App's perspective (React Widget)
```
1. Host pastes one <script> tag into their page
2. Bubble appears bottom-right, themed with their primary color
3. Click → chat panel expands
4. User types → streamed response appears word by word
5. Widget resizes iframe via postMessage to fit content
6. If host not in allowed_origins → browser blocks iframe entirely (CSP)
```

### From a Friday Demo perspective (what you show)
```
Demo 1: Widget on allowed host → works ✓
Demo 2: Widget on blocked host → browser console shows CSP block ✗
Demo 3: Langfuse UI → walk a real trace tree (LLM → tool → RAG spans)
Demo 4: One trace with an error path (classifier down → graceful fallback)
Demo 5: Cross-conversation recall — new session, bot references prior conversation
Demo 6: Add a new endpoint live (proves architecture layers)
```

### What the system is NOT
- Not a workflow / pipeline that always runs all steps
- Not multi-agent — one LLM decides everything
- Not Streamlit-in-iframe for the widget
- No auto memory writes — user/LLM must explicitly call write_memory

---

## RULES (grading contract)

| Rule | Meaning |
|------|---------|
| No vibe coding | Understand every line — live questions on Friday |
| Architecture IS the grade | Clean layers > feature-complete mess |
| Evals ARE the grade | CI fails on regression, thresholds committed |
| Every decision backed by a number | DECISIONS.md is evidence, not opinion |
| Logs redacted, traces real | Proved by tests and live Langfuse demo |

---

## SUBMISSION FORMAT

```
Project 7 - [Name]
Repo: [GitHub URL]
Tag: v0.1.0-week7
Dataset: pandas issues, [N train / N val / N test]
Classification — Classical: F1=[n] | Fine-tuned: F1=[n] | LLM: F1=[n]
Deployment choice: [model] - because [one line]
Embedding model: [name] - chosen because [one line]
RAG — hit@5=[n] | MRR@10=[n] | Faithfulness=[n] | Answer relevancy=[n]
Long-term memory type: episodic
Tracing backend: Langfuse - chosen because [one line]
Widget bundle size: [n] KB (gzipped)
LLM: [provider + model]
README contains: ARCH.md, DECISIONS.md, RUNBOOK.md, EVALS.md, SECURITY.md
```

---

## HARD DESIGN QUESTIONS (must have answers for Friday)

1. **Three models, one production** — which ships? Does answer change with scale/latency/failure cost?
2. **Embedding model** — how do you know it's right for THIS corpus vs benchmark it was advertised on?
3. **LLM-judge disagrees with you** — who's right? What do you do with the judge in CI?
4. **Redis TTL** — what's the value, why that number, what happens at the boundary?
5. **Widget bundle size** — what can you cut, what's the cost? At what size do you push back?
6. **GitHub token in chat** — where does it end up if redaction misses it? How would you find out first?
7. **4.3s span** — what's missing from the trace, and why is that a design decision?
8. **Vault unreachable at runtime** (already running) — what happens, what SHOULD happen, where does policy live?

---

## KEY CONNECTIONS SUMMARY

```
Dataset ──────────────────────────────────────────────────────────────────┐
  train/val/test ────────────────→ Model training → distilbert_weights    │
  rag_corpus ────────────────────→ Chunking → pgvector (embeddings)       │
  golden sets ───────────────────→ Eval scripts → CI gates                │
                                                                           │
modelserver ←─────────────────────────────────────────────────────────────┤
  /classify ←── api.tools.classify_issue ←── LLM tool call               │
  /ner      ←── api.tools.extract_entities ←── LLM tool call             │
  /summarize ←─ api.tools.summarize ←── LLM tool call                    │
  /rerank   ←── api.services.rag_service ←── LLM tool call (rag_search)  │
                                                                           │
api (FastAPI) ─────────────────────────────────────────────────────────── ┤
  ← chatbot (Streamlit) calls HTTP                                        │
  ← widget (React) calls HTTP                                             │
  → Redis (short-term memory)                                             │
  → pgvector (long-term memory + RAG chunks)                              │
  → MinIO (eval reports, chunk snapshots)                                 │
  → Vault (all secrets at startup)                                        │
  → Langfuse (all traces)                                                 │
  → modelserver (all NLP inference)                                       │
                                                                           │
CI (every push):                                                           │
  lint → build → eval classification → eval RAG → redaction test → smoke ─┘
```
