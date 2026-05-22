# ARCH.md — System Architecture

## Overview

Maintainer's AI Copilot is a single-LLM tool-calling chatbot that helps open-source maintainers triage GitHub issues. The architecture is a layered FastAPI monolith backed by PostgreSQL (pgvector), Redis, MinIO, and Vault, with a separate modelserver container for CPU-bound ML inference.

---

## Service Map

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser / Host Page                                             │
│  host:3001  ──embed script──▶  widget:5173 (iframe)             │
│                                     │ JWT + SSE                 │
│  chatbot:8501 (Streamlit) ──────────┤                           │
└────────────────────────────────────┼─────────────────────────────┘
                                     │ HTTP
                              ┌──────▼──────┐
                              │   api:8000  │  FastAPI
                              │  (routes /  │
                              │  services / │
                              │  repos)     │
                              └──┬──┬──┬───┘
              ┌──────────────────┘  │  └──────────────────┐
              ▼                     ▼                      ▼
     ┌────────────────┐   ┌─────────────────┐   ┌─────────────────┐
     │   db:5432      │   │  redis:6379     │   │  minio:9000     │
     │ pgvector/pg16  │   │  Redis 7        │   │  MinIO          │
     │ (app + langfuse│   │  conversation   │   │  model weights  │
     │  schemas)      │   │  cache / TTL    │   │  eval reports   │
     └────────────────┘   └─────────────────┘   └─────────────────┘
              │
     ┌────────▼────────┐
     │  modelserver    │  FastAPI on :8001
     │  :8001          │  DistilBERT classifier
     │                 │  spaCy NER
     │                 │  ms-marco reranker
     └─────────────────┘

     ┌─────────────────┐   ┌─────────────────┐
     │  vault:8200     │   │  langfuse:3000   │
     │  HashiCorp Vault│   │  self-hosted     │
     │  (all secrets)  │   │  LLM tracing     │
     └─────────────────┘   └─────────────────┘
```

---

## Layer Architecture (API)

Requests flow strictly downward — no layer skips another:

```
api/app/api/routes/     ← HTTP routing, auth, request/response serialisation
api/app/services/       ← Business logic, transactions, orchestration
api/app/repositories/   ← SQL queries only (SQLAlchemy async)
api/app/domain/         ← Pydantic domain models (shared contracts)
api/app/infra/          ← Vault, Redis, MinIO, LLM, redaction adapters
```

---

## Request Flow: Chat Turn

```
POST /chat/stream  (Bearer JWT)
  │
  ▼ TraceIDMiddleware (main.py)
  │  generates UUID trace_id → binds to structlog context → X-Trace-ID header
  │
  ▼ auth dependency → verify JWT → load UserOut
  │
  ▼ chat_service.stream_chat()
  │  1. Load conversation history from Redis (key: conversation:{user_id}:{cid})
  │  2. Embed user message → retrieve top-3 semantic memories from pgvector
  │  3. Build system prompt with {memories} injected
  │  4. Create Langfuse trace (trace_id bound)
  │  5. _run_tool_loop(messages, ctx, trace)
  │      ├─ iter 0: tool_choice="required" (forces ≥1 tool call)
  │      ├─ OpenAI gpt-4o-mini (streaming=False for tool loop)
  │      │    └─ trace.generation() child span per iteration
  │      ├─ For each tool call:
  │      │    ├─ classify_issue  → POST modelserver:8001/classify
  │      │    ├─ extract_entities → POST modelserver:8001/ner
  │      │    ├─ summarize_thread → LLM call (prompts/summarize.md)
  │      │    ├─ search_knowledge_base → hybrid_search() → rerank()
  │      │    │    ├─ HyDE: LLM generates hypothetical answer
  │      │    │    ├─ embed(0.5*query + 0.5*hyde) → pgvector cosine
  │      │    │    ├─ FTS tsvector → hybrid score (0.6 dense + 0.4 sparse)
  │      │    │    └─ cross-encoder rerank top-20 → top-5
  │      │    └─ write_memory → embed + upsert long_term_memories
  │      │    └─ trace.span() child span per tool call
  │      └─ loop until no tool calls (max 6 iterations)
  │  6. Stream final response tokens via SSE
  │  7. Append messages to Redis (TTL reset to 24h)
  │  8. Persist assistant turn to DB (conversations / messages tables)
  │  9. Structlog JSON line with trace_id, latency, token counts (all redacted)
  │
  ▼ SSE stream: data: {"type":"token","content":"..."}\n\n
                data: {"type":"done","label":"bug","sources":[...]}\n\n
```

---

## RAG Pipeline Detail

```
User query
  │
  ├─ HyDE transform (prompts/hyde.md)
  │    LLM generates hypothetical resolved-issue passage
  │
  ├─ Dual embedding
  │    embed(query)        → vector_q
  │    embed(hypothetical) → vector_h
  │    search_vec = 0.5 × vector_q + 0.5 × vector_h
  │
  ├─ Hybrid retrieval (single SQL)
  │    SELECT id, text,
  │      (0.6 * (1 - embedding <-> search_vec) +
  │       0.4 * ts_rank(search_vector, plainto_tsquery(query))) AS score
  │    FROM chunks
  │    WHERE label = $current_label OR source = 'docs'
  │    ORDER BY score DESC LIMIT 20;
  │
  ├─ Cross-encoder rerank (ms-marco-MiniLM-L-6-v2)
  │    Score each (query, chunk_text) pair → sort → top 5
  │
  └─ Return parent chunks (1024 tokens) to LLM context
       Child chunks (256 tokens) were used for retrieval precision
```

---

## Chunking Schema

```sql
chunks (
    id          UUID PRIMARY KEY,
    parent_id   UUID REFERENCES chunks(id),   -- NULL for parent chunks
    text        TEXT NOT NULL,
    embedding   vector(1536),                 -- text-embedding-3-small
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    source      TEXT,                         -- 'docs' | issue URL
    issue_id    TEXT,
    label       TEXT,                         -- collection label
    is_parent   BOOLEAN DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT now()
)
-- Indexes:
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
CREATE INDEX ON chunks USING GIN(search_vector);
```

---

## Long-term Memory

```sql
long_term_memories (
    id         UUID PRIMARY KEY,
    user_id    UUID REFERENCES users(id),
    summary    TEXT NOT NULL,
    embedding  vector(1536),
    created_at TIMESTAMPTZ DEFAULT now()
)
```

- Written only when `write_memory` tool is called (explicit user instruction)
- Retrieved at turn start: top-3 by cosine similarity to current issue text
- Injected into system prompt: `{memories}` placeholder

---

## Widget Embed Flow

```
host page (3001)
  └─ <script src="http://localhost:8000/widget.js" data-widget-id="UUID">
        │
        ├─ loader injects <iframe src="http://localhost:5173/?widget_id=UUID&api_url=http://localhost:8000">
        │
        └─ iframe (widget:5173 nginx) serves widget/index.html
              ├─ GET /widget-config/{UUID} → applies runtime theme (CSS vars, position)
              ├─ POST /auth/login → JWT stored in sessionStorage
              ├─ POST /conversations → creates conversation
              └─ POST /chat/stream → SSE streaming
                    └─ postMessage({type:"copilot-resize", widgetId, height}, "*") to host
```

---

## Secrets Management

All runtime secrets live in Vault at `secret/data/copilot`. The `.env` file holds only bootstrap variables (`VAULT_ADDR`, `VAULT_TOKEN`) — never actual secrets.

```
Vault path: secret/data/copilot
  openai_api_key        ← GPT-4o-mini + text-embedding-3-small
  db_password           ← PostgreSQL
  minio_secret_key      ← MinIO object store
  langfuse_public_key   ← Langfuse tracing
  langfuse_secret_key   ← Langfuse tracing
```

The `vault-init` one-shot container writes secrets from `.env` into Vault on first boot. The API reads them via `app/infra/vault.py` at startup.

---

## Observability

- **Tracing:** Langfuse (self-hosted on :3000). Every request gets a parent trace. Each LLM iteration is a `generation` child span. Each tool call is a `span` child.
- **Structured logging:** structlog JSON to stdout. Every log line carries `trace_id` (bound by `TraceIDMiddleware`) and passes through `_redacting_processor` before emission.
- **Metrics:** Exposed at `GET /health` (liveness). Token counts and latency logged per request.

---

## Key Design Decisions

| Decision | Choice | Primary reason |
|---|---|---|
| LLM | GPT-4o-mini | Best tool-calling at $0.15/1M tokens |
| Embeddings | text-embedding-3-small | MTEB 62.3, same API key, $0.05 corpus cost |
| Vector store | pgvector HNSW | Already in stack, hybrid retrieval in one SQL |
| Sparse retrieval | PostgreSQL FTS | Same DB, GIN index, no extra service |
| Reranker | ms-marco-MiniLM-L-6-v2 | Single biggest RAG quality improvement |
| Query transform | HyDE 50/50 | Closes query/answer vector gap |
| Chunking | Hierarchical 256/1024 | Sharp embeddings + rich LLM context |
| Classifier | DistilBERT fine-tune | 0.8867 macro-F1, zero per-call cost |
| Tracing | Langfuse | LLM-native spans, no extra container (self-hosted) |
| Memory | Semantic pgvector | Cross-session fact recall, demonstrable in demo |

Full justifications: see [DECISIONS.md](resources/DECISIONS.md).
