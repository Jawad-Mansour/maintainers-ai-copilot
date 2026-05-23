# Architecture

## Service Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Host Browser                               │
│  host:3001 ──<script src="/widget.js">──► widget:5173 (iframe)     │
│  chatbot:8501 (Streamlit)                                           │
└──────────────┬──────────────────────────────────┬───────────────────┘
               │ HTTP/SSE                          │ HTTP/SSE
               ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        api:8000 (FastAPI)                            │
│  /auth   /chat/stream   /memories   /rag   /widgets   /widget.js    │
│                                                                      │
│  DynamicCORSMiddleware ◄── _cors_origins (env ∪ DB widget origins)  │
│  TraceIDMiddleware      ◄── X-Trace-ID on every response            │
│  AppError handler       ◄── {code, message, request_id} → no trace  │
└────┬────────────┬──────────────┬──────────────┬──────────────────────┘
     │            │              │              │
     ▼            ▼              ▼              ▼
 db:5432      redis:6379     minio:9000     modelserver:8001
 (pgvector)   (history +     (weights +     (classify / NER /
              cache)         evals +        summarize / rerank)
                             snapshots)
     │
     ▼
 vault:8200
 (all secrets fetched at startup, never per-request)

 langfuse:3000
 (every LLM call / tool call / RAG retrieval = a span)
```

---

## Code Layer Boundaries

Enforced in every route — no layer skips another:

```
api/app/
├── api/routes/      HTTP only — FastAPI routers, request/response models
│                    No SQLAlchemy. No Redis. No external HTTP.
│
├── services/        Business logic, transaction boundaries, cache ops
│                    Calls repositories for SQL and infra for adapters.
│
├── repositories/    SQL only — raw SELECT/INSERT via SQLAlchemy text()
│                    No HTTP errors. No cache invalidation.
│
├── domain/          Pydantic models (input/output shapes)
│                    Distinct from SQLAlchemy ORM models in infra/db/
│
└── infra/           Adapters — one file per external system
    ├── vault.py         fetch_vault_secrets()
    ├── openai_client.py embed_texts(), embed_one()
    ├── redis_client.py  build_redis()
    ├── minio_client.py  build_minio(), save_chunk_snapshot()
    ├── modelserver_client.py classify(), ner(), summarize(), rerank()
    ├── observability.py configure_logging(), get_logger(), bind_trace_id()
    ├── redaction.py     redact()
    ├── jwt_handler.py   create_token(), decode_token()
    └── prompts.py       load_prompt(name)
```

---

## Startup Sequence

```
vault ──healthy──► vault-init (one-shot: writes secrets)
db   ──healthy──► langfuse-db-init (one-shot: creates langfuse_db)
                  migrate (one-shot: alembic upgrade head → exits)
minio──healthy──► minio-init (one-shot: creates buckets)

                  modelserver ──healthy──►
                  (loads DistilBERT + LR weights from MinIO, verifies SHA-256)

                  api ──── refuses to boot if ANY of:
                    ✗ eval_thresholds.yaml missing or any threshold = 0
                    ✗ Vault unreachable
                    ✗ modelserver unhealthy
                    ✗ modelserver in mock mode (REQUIRE_REAL_MODELSERVER=true)
                    ✗ Langfuse auth_check() fails

chatbot / widget / host start after api is healthy.
```

---

## Full Request Flow — One User Message

```
POST /chat/stream  {conversation_id, message}   Bearer JWT
        │
        ├─ 1. JWT decode → user_id, role         [infra/jwt_handler.py]
        │
        ├─ 2. Redis GET conversation:{id}         [services/chat_service.py]
        │      → message history (last N turns)
        │      TTL: 86,400 s (24 h)
        │
        ├─ 3. pgvector recall long-term memories  [repositories/memory_repo.py]
        │      → top-3 episodic memories by cosine similarity
        │      (semantic search on user's stored memories)
        │
        ├─ 4. Build prompt                        [infra/prompts.py]
        │      system.md + memories + history + user message
        │
        ├─ 5. LLM loop (max 5 iterations)         [services/chat_service.py]
        │      GPT-4o-mini, tool_choice="auto"
        │
        │      ── tool call: classify_issue ──────────────────────────────┐
        │      │   redact(inputs)                                         │
        │      │   POST modelserver:8001/classify                         │
        │      │   → {label, confidence}                                  │
        │      │   redact(outputs)                                        │
        │      │   Langfuse span: tool=classify_issue, latency, tokens   ◄┘
        │
        │      ── tool call: rag_search ───────────────────────────────────┐
        │      │   → rag_service.search()  [see RAG pipeline below]        │
        │      │   Langfuse span: tool=rag_search                         ◄┘
        │
        │      ── tool call: write_memory ─────────────────────────────────┐
        │      │   embed(text) → pgvector INSERT into memories             │
        │      │   audit_log INSERT: actor=user_id, action=write_memory   ◄┘
        │
        ├─ 6. Stream final response (SSE)
        │      data: {"type": "token", "content": "..."}
        │      data: {"type": "done", "label": "...", "sources": [...]}
        │
        └─ 7. Persist (try/finally — runs even on client disconnect)
               Redis SET conversation:{id} updated_history  TTL=86400s
               Postgres INSERT messages (user + assistant)
               db.commit()
               Langfuse flush trace
```

---

## RAG Pipeline

```
User query
    │
    ├─ embed_one(query)                     text-embedding-3-small, 1536 dims
    │
    ├─ HyDE: GPT-4o-mini generates a        [hyde.md prompt]
    │   hypothetical answer to the query
    │   → embed_one(hypothetical_answer)
    │
    ├─ combined_vec = (query_vec + hyp_vec) / 2.0
    │
    ├─ hybrid_search(combined_vec, query_text, top_k=20)
    │      ┌─ dense: pgvector cosine sim on child chunks (embedding IS NOT NULL)
    │      │         ORDER BY embedding <=> combined_vec LIMIT 20
    │      └─ sparse: ts_rank(search_vector, plainto_tsquery(query_text))
    │         score = 0.6 × dense + 0.4 × sparse
    │         metadata filter: label=req.label, source=req.source (optional)
    │
    ├─ for each candidate: fetch parent_text from chunks WHERE id=parent_id
    │   (parent: 1024 tokens — passed to LLM as context)
    │   (child: 256 tokens  — was used for retrieval)
    │
    ├─ POST modelserver:8001/rerank         cross-encoder/ms-marco-MiniLM-L-6-v2
    │   → scores for all 20 candidates
    │   → sort descending, take top_k (default 5)
    │
    ├─ save_chunk_snapshot(minio, conversation_id, top_k_chunks)
    │   bucket: chunk-snapshots
    │   (last N conversations retrievable for debugging)
    │
    └─ return list[ChunkResult] to caller
```

---

## Deep Learning Track — Model Server

```
modelserver:8001 (FastAPI)
    │
    ├─ POST /classify  {text}
    │      DistilBERT fine-tuned 4-class (distilbert-base-uncased)
    │      Frozen: embedding + layers 0–3
    │      Trainable: layers 4–5 + pre-classifier + head
    │      → {label, confidence}
    │
    ├─ POST /classify/classical  {text}
    │      TF-IDF (max 50K features, ngram 1–2) + Logistic Regression
    │      → {label, confidence}
    │
    ├─ POST /ner  {text}
    │      spaCy en_core_web_sm + EntityRuler
    │      Patterns: Python package names, error types, function names
    │      → {entities: [{text, label, start, end}]}
    │
    ├─ POST /summarize  {text}
    │      GPT-4o-mini + summarize.md prompt
    │      → {summary}
    │
    └─ POST /rerank  {query, passages: [str]}
           cross-encoder/ms-marco-MiniLM-L-6-v2
           → {scores: [float]}

Boot sequence:
    MinIO models bucket → download_and_verify()
    → SHA-256 check vs model_card.json
    → WeightsNotFound → start in mock mode (REQUIRE_REAL_MODELSERVER=false to allow)
    → SHA mismatch → RuntimeError (hard fail, refuse to boot)
```

---

## Authentication Flow

```
POST /auth/register  {email, password}
    → bcrypt hash, INSERT users, return access_token

POST /auth/login  {email, password}
    → bcrypt verify, JWT signed with secret from Vault
    → access_token (HS256, 24 h expiry)

JWT payload: {sub: user_id, role: "user"|"admin", exp}

Protected routes: Depends(get_current_user)
    → decode JWT → user_id, role
    → role check inline (admin endpoints: require_admin dep)

Admin-only:
    POST /admin/invite        create user with specified role
    GET  /admin/audit-log     return audit_log rows
    POST /widgets             create widget config
    DELETE /widgets/{id}      delete widget
```

---

## Widget Embed Flow

```
Host page (host:3001/index.html):
    <script>
      (async () => {
        const { id } = await fetch('http://localhost:8000/widget-default').then(r=>r.json());
        const s = document.createElement('script');
        s.src = 'http://localhost:8000/widget.js';
        s.dataset.widgetId = id;
        document.body.appendChild(s);
      })();
    </script>

/widget.js (loader):
    reads data-widget-id from document.currentScript
    injects: <iframe src="http://localhost:5173/?widget_id=<id>&api_url=<api>">
    style: fixed bottom-right, 400×80px, transitions to 400×600px when open
    listens: window.message { type:'copilot-resize' } → iframe.style.height

widget:5173/index.html (vanilla JS, ~20 KB raw, ~6 KB gzip):
    GET /widget-config/{widget_id}
        → reads {theme, greeting, enabled_tools}
        → applies CSS custom properties for primary_color
        → applies position (bottom-left / bottom-right)
    Login view → POST /auth/login → JWT stored in sessionStorage
    Chat view  → POST /conversations → POST /chat/stream (SSE)
    postMessage copilot-resize to parent on panel open/close

Security:
    GET /widget-config/{id} checks Origin header vs allowed_origins in DB
    CSP frame-ancestors header = allowed_origins (browser blocks unauthorized parents)
    CORS _cors_origins = env var list ∪ all widget allowed_origins (loaded at startup)
```

---

## Observability

```
Every request:
    TraceIDMiddleware → binds trace_id (UUID4) to structlog context
    → X-Trace-ID response header
    → every log line includes trace_id field

Langfuse trace tree per conversation:
    root span: user_message (conversation_id, user_id)
    ├─ span: llm_call (model=gpt-4o-mini, prompt_tokens, completion_tokens, latency)
    ├─ span: tool_call (tool=classify_issue, inputs_redacted, outputs_redacted, latency)
    ├─ span: rag_search (query, n_chunks, top_score, latency)
    └─ span: tool_call (tool=write_memory, actor, latency)

Structured logging (structlog JSON):
    every log line: {event, trace_id, timestamp, level, module}
    redaction runs before any log write or span attribute set

Redaction patterns (SECURITY.md for full list):
    OpenAI keys (sk-...), GitHub tokens (ghp_...), emails, IPv4 addresses
```

---

## Exception Hierarchy

```
AppError (base, HTTP 500)
├── NotFoundError     → 404
├── PermissionDenied  → 403
├── ConflictError     → 409
└── ToolFailure       → 422

Single handler at API boundary:
    @app.exception_handler(AppError)
    → JSONResponse {error: code, message: str, request_id: UUID}
    No stack trace in response.

Tool failure recovery in chat_service.py:
    except ToolFailure as exc:
        tool_result = f"Tool failed: {exc}. Continuing without this result."
    LLM receives the failure message and continues — does not 500.

Uncaught exception handler:
    @app.exception_handler(Exception)
    → 500, {error: "internal_error", request_id: UUID}
    → logger.exception() with trace_id (stack trace in logs, not in response)
```
