# Phase 4 — Chatbot Agent + Memory

**Status:** ✅ Complete (2026-05-20)
**Commit:** `feat: phase 4 — chatbot agent, long-term memory, Langfuse tracing`

---

## Goal

Wire the full end-to-end chat loop: a user sends a message, the system classifies it,
retrieves relevant knowledge base chunks (Phase 3), injects memories from past conversations,
calls GPT-4o-mini, stores the exchange, and saves a memory for future sessions. Every LLM
generation is traced in Langfuse cloud.

---

## Why This Phase Exists

Phase 3 gave us a retrieval pipeline with no consumer. Phase 4 connects retrieval to the LLM
and gives the system a real-time interface through `POST /chat`. After Phase 4, the system
can answer questions about GitHub issues using its knowledge base.

Without Phase 4:
- The RAG pipeline has no caller — ingested chunks are never used
- There is no memory across conversations — the assistant forgets everything
- Langfuse traces are empty — no observability into LLM behavior
- Phase 5 widget has no backend chat endpoint to call

---

## Files Created / Modified

### New files

| File | Purpose |
|------|---------|
| `api/app/repositories/memory_repo.py` | Semantic search on memories table + insert |
| `api/app/services/memory_service.py` | Retrieve relevant past memories, embed + persist new ones |
| `api/app/services/chat_service.py` | Full chat pipeline: classify → retrieve → generate → persist |
| `api/app/api/routes/chat.py` | POST /chat |

### Modified files

| File | Change |
|------|--------|
| `api/app/domain/models.py` | Added ChatRequest, ChatResponse |
| `api/config.py` | Added `modelserver_host` setting |
| `api/main.py` | ModelServerClient + Langfuse in lifespan; chat_router included; langfuse.flush() on shutdown |

---

## Detailed File Breakdown

### `api/app/repositories/memory_repo.py`

```python
async def search_by_similarity(
    db: AsyncSession,
    user_id: uuid.UUID,
    query_vec: list[float],
    top_k: int = 3,
) -> list[dict[str, Any]]
# SQL: SELECT id, summary, 1-(embedding<=>:vec) AS score FROM memories
#      WHERE user_id = :user_id
#      ORDER BY embedding <=> CAST(:vec AS vector)
#      LIMIT :top_k
# Returns dicts with keys: id, summary, score

async def create(
    db: AsyncSession,
    user_id: uuid.UUID,
    summary: str,
    embedding: list[float],
) -> Memory
# Adds Memory ORM object to session (no commit — service layer commits)
```

**Full SQL for memory search:**

```sql
SELECT id, summary,
       1 - (embedding <=> CAST(:vec AS vector)) AS score
FROM memories
WHERE user_id = :user_id
ORDER BY embedding <=> CAST(:vec AS vector)
LIMIT :top_k
```

**Why filter by `user_id`?**
Memories are private — they capture what each user has asked about in the past. User A's
past conversations about numpy should not appear as context for User B's query. Every memory
row has a `user_id` FK from Phase 1.

**Why `1 - distance` as score?**
`<=>` returns cosine *distance* (0 = identical, 2 = opposite). Converting to similarity
(`1 - distance`) gives a score where higher is better, consistent with how `chunk_repo`
returns scores.

**Why raw SQL instead of ORM?**
Same reason as chunk_repo — `<=>` (pgvector cosine distance operator) is not expressible
in SQLAlchemy ORM without raw SQL.

**Vector format:** same as `chunk_repo`:
```python
vec_str = f"[{','.join(map(str, query_vec))}]"
```

---

### `api/app/services/memory_service.py`

```python
async def get_relevant_memories(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    api_key: str,
    top_k: int = 3,
) -> list[str]
# 1. embed_one(query, api_key) → query_vec
# 2. memory_repo.search_by_similarity(db, user_id, query_vec, top_k)
# 3. Return [row["summary"] for row in rows]
# Returns empty list if user has no memories yet

async def save_memory(
    db: AsyncSession,
    user_id: uuid.UUID,
    summary: str,
    api_key: str,
) -> None
# 1. embed_one(summary, api_key) → embedding
# 2. memory_repo.create(db, user_id, summary, embedding)
# No commit — caller (chat_service) commits everything atomically
```

**What is stored as the "memory"?**
The user's message, truncated to 500 characters:
```python
await memory_service.save_memory(db, user_id, req.message[:500], api_key)
```

**Why store the user message and not a generated summary?**
Generating a summary requires an extra GPT-4o-mini call (latency ~300ms + token cost).
The user's message is already a dense semantic signal — it's a question or description that
naturally embeds into a meaningful vector. When the same user asks a related question later,
the past query's embedding will score highly, injecting the relevant past context.

A future improvement (Phase 6+) could replace this with a GPT-4o-mini summarization of
the full exchange — but for Phase 4 correctness, the raw message is sufficient.

**Why 3 memories?**
3 bullet points in the system prompt adds ~150 tokens of context. That's meaningful without
being wasteful. The HNSW index on `memories.embedding` (Phase 1) makes retrieval fast
regardless of how many memories the user has accumulated.

**Why is embedding re-computed at save time?**
The embedding is computed from the summary text at save time, not cached. This ensures
the embedding is always consistent with the actual text stored. If the embedding model
changes in a future phase, old memories remain retrievable because their vectors match
the model used at insertion time (the new model would need a re-embedding migration).

---

### `api/app/services/chat_service.py`

```python
async def chat(
    db: AsyncSession,
    req: ChatRequest,          # message: str, conversation_id: UUID
    user_id: uuid.UUID,
    api_key: str,
    minio_client: Minio,
    modelserver_client: ModelServerClient,
    langfuse: Langfuse,
) -> ChatResponse              # reply: str, label: str, sources: list[str]
```

**The 7-step pipeline:**

```
Step 1: CLASSIFY
  modelserver_client.classify([req.message])
  → label: str  (e.g. "bug", "feature", "question", "documentation")

Step 2: MEMORY RETRIEVAL
  memory_service.get_relevant_memories(db, user_id, req.message, api_key, top_k=3)
  → memories: list[str]  (summaries of past messages)

Step 3: RAG SEARCH
  rag_service.search(db, SearchRequest(
      query=req.message,
      conversation_id=req.conversation_id,
      label=label if label != "unknown" else None,
      top_k=5,
  ), api_key, minio_client)
  → chunks: list[ChunkResult]  (with parent_text for context)

Step 4: CONVERSATION HISTORY
  message_repo.list_by_conversation(db, req.conversation_id)
  → history[-10:]  (last 10 messages, chronological ASC order)

Step 5: PROMPT ASSEMBLY
  system prompt = _SYSTEM_PROMPT.format(label, memories_text, chunks_text)
  messages = [system] + history_messages + [current_user_message]

Step 6: LLM GENERATION
  Langfuse trace + generation span opened
  AsyncOpenAI.chat.completions.create(
      model="gpt-4o-mini",
      messages=messages,
      max_tokens=512,
      temperature=0.3,
  )
  generation.end(output=reply)

Step 7: PERSIST + MEMORY
  message_repo.create(db, conversation_id, "user", req.message)
  message_repo.create(db, conversation_id, "assistant", reply)
  memory_service.save_memory(db, user_id, req.message[:500], api_key)
  db.commit()   ← single atomic commit for all three writes
```

**Why classify before RAG?**
The `label` is passed as a metadata `WHERE` filter to `chunk_repo.hybrid_search`. A "bug"
query searches only `WHERE label = 'bug'`, retrieving issue chunks about bugs rather than
feature requests or docs. Without classification, all labels compete for the top-5 slots,
diluting retrieval precision.

When label is "unknown" (modelserver unavailable or low-confidence), no filter is applied
(`label=None` → `WHERE :label IS NULL` → all chunks considered).

**System prompt template:**

```python
_SYSTEM_PROMPT = """\
You are a GitHub maintainer copilot helping with {label} issues.

Relevant memories from past conversations:
{memories}

Relevant knowledge base:
{chunks}

Answer concisely and accurately. If unsure, say so.\
"""
```

Rendering example:
```
You are a GitHub maintainer copilot helping with bug issues.

Relevant memories from past conversations:
- How to fix SettingWithCopyWarning in pandas 2.0?
- Why does DataFrame.loc behave differently on MultiIndex?

Relevant knowledge base:
[bug] Setting values on a slice from a DataFrame is ambiguous. You should
use .loc[row_indexer, col_indexer] = value instead of chained indexing...

[bug] The copy warning indicates that pandas cannot determine whether the
operation is on a copy or the original DataFrame...

Answer concisely and accurately. If unsure, say so.
```

**Why `temperature=0.3`?**
Lower temperature (0.0–0.3) produces more deterministic, factual answers — appropriate for
technical question answering where creativity is undesirable. Higher temperature (0.7–1.0)
is for generative tasks. Issue triage is a factual domain.

**Why `max_tokens=512`?**
Enough for a detailed technical answer (~400 words) without generating padding. GPT-4o-mini's
context window is 128K tokens, so this is a conservative output limit. If the answer requires
more detail, the user can ask a follow-up.

**Why `history[-10:]`?**
10 messages = 5 turns. This keeps the context window from growing indefinitely. Older history
is implicitly captured by the memory system (past sessions' summaries are retrieved via semantic
search). The `-10:` slice takes the most recent messages in ascending chronological order
(already sorted ASC by `message_repo.list_by_conversation`).

**Prompt message list format:**
```python
[
    {"role": "system",    "content": "You are a GitHub maintainer copilot..."},
    {"role": "user",      "content": "How do I fix SettingWithCopyWarning?"},
    {"role": "assistant", "content": "Use .loc instead of chained indexing..."},
    {"role": "user",      "content": "What about with .iloc?"},  # current
]
```

**Why single `db.commit()` for messages + memory?**
If the commit fails after writing the user message but before writing the assistant reply,
the DB would contain an orphaned user message with no response — causing the conversation
to replay incorrectly on the next request. One atomic commit ensures all three writes
(user message, assistant message, memory) succeed or all fail together.

**Sources deduplication:**
```python
sources=list({c.source for c in chunks if c.source})
```
A set comprehension deduplicates sources (e.g., if 3 chunks all came from "pandas/issues",
the response only lists it once). The order is not guaranteed but that's acceptable for a
source attribution list.

---

### `api/app/repositories/memory_repo.py` — detailed

The `Memory` ORM model (Phase 1):

```python
class Memory(Base):
    __tablename__ = "memories"
    id: UUID (PK)
    user_id: UUID (FK → users.id CASCADE DELETE)
    summary: Text
    embedding: Vector(1536)   # HNSW index: m=16, ef_construction=64, cosine_ops
    created_at: DateTime (server_default)
```

When a user is deleted (CASCADE DELETE), all their memories are automatically removed.
This satisfies GDPR right-to-erasure without any application-level cleanup code.

---

### `api/app/api/routes/chat.py`

```python
router = APIRouter(prefix="/chat", tags=["chat"])

def _get_minio(request: Request) -> Minio
def _get_modelserver(request: Request) -> ModelServerClient
def _get_langfuse(request: Request) -> Langfuse
# All read from request.app.state.* — same pattern as get_db / get_redis

MinioDep = Annotated[Minio, Depends(_get_minio)]
ModelServerDep = Annotated[ModelServerClient, Depends(_get_modelserver)]
LangfuseDep = Annotated[Langfuse, Depends(_get_langfuse)]

@router.post("", response_model=ChatResponse)
async def chat(req, db, secrets, minio, modelserver, lf, user) -> ChatResponse
```

**Why are minio/modelserver/langfuse dependencies defined as local functions here?**
These read from `request.app.state`, which is only available inside a request context.
They can't be defined in `dependencies.py` without importing the client types there,
which would create circular dependency risks. Keeping them local to the route file that
needs them is clean and consistent with the pattern in `rag.py`.

**Why `lf` not `langfuse` as the parameter name?**
`langfuse` would shadow the module-level import `from langfuse import Langfuse`. Using
`lf` avoids the shadowing while keeping the code readable.

---

## Langfuse Tracing

**Client initialization (main.py lifespan):**

```python
app.state.langfuse = Langfuse(
    public_key=secrets.langfuse_public_key,    # from Vault secret/langfuse
    secret_key=secrets.langfuse_secret_key,    # from Vault secret/langfuse
    host=secrets.langfuse_host,                # "https://cloud.langfuse.com"
)
```

**Trace structure per chat call:**

```
Langfuse trace "chat"
├── metadata:
│   ├── user_id: "3fa85f64-..."
│   ├── conversation_id: "abc123..."
│   └── label: "bug"
│
└── generation "gpt-4o-mini"
    ├── model: "gpt-4o-mini"
    ├── model_parameters: {max_tokens: 512, temperature: 0.3}
    ├── input: [system_msg, history..., user_msg]
    └── output: "assistant reply text..."
```

**Why `langfuse.flush()` on shutdown?**
Langfuse buffers events and sends them asynchronously in batches. If the process exits
before the buffer is flushed, the last few traces are lost. `flush()` blocks until all
pending events are sent. Called in the lifespan exit (after `yield`).

**What's observable in Langfuse dashboard:**
- Every GPT-4o-mini call with full input/output
- Token usage per generation
- Latency per trace
- Which user triggered each trace (for debugging)
- Conversation-level grouping (all traces with the same `conversation_id` tag)

**Langfuse keys come from Vault — never env vars:**
```python
# WRONG (never do this):
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-real-key"

# CORRECT (our implementation):
Langfuse(secret_key=secrets.langfuse_secret_key)  # from VaultSecrets
```

---

## Domain Models Added

```python
class ChatRequest(BaseModel):
    message: str          # User's message (question, bug report, etc.)
    conversation_id: UUID # Must be an existing conversation owned by the user

class ChatResponse(BaseModel):
    reply: str            # GPT-4o-mini's answer
    label: str            # Predicted issue label from modelserver
    sources: list[str]    # Deduplicated list of source strings from retrieved chunks
```

**Why no `conversation_id` in `ChatResponse`?**
The client already knows it — they sent it in the request. Echoing it back would be
redundant. If the client needs to confirm it was used, they can call
`GET /conversations/{conv_id}/messages`.

**Why is `label` in the response?**
The frontend (Phase 5 Streamlit admin / React widget) can display the predicted issue
category as a tag next to the assistant's reply. Also useful for debugging — if the
model returns wrong labels, it's immediately visible in the response.

---

## API Endpoint

### POST /chat

```
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "message": "Why does iloc raise IndexError on a valid index?",
  "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}

→ 200 OK
{
  "reply": "This typically happens when the DataFrame index is not contiguous after filtering...",
  "label": "bug",
  "sources": ["pandas/issues"]
}
```

**Error cases:**
- `401` — missing or invalid JWT
- `401` — user account inactive
- `404` — conversation not found (raised by Phase 2's conversation ownership check)
  Note: currently not checked in Phase 4 — `message_repo.create` will fail with a FK
  constraint violation if `conversation_id` doesn't exist, returning a 500.
  A Phase 4-T fix: add `conversation_repo.get` ownership check at the start of `chat_service`.
- `502` — modelserver unreachable (raises `ToolFailure`, caught by `AppError` handler → 502)
- `500` — OpenAI API down (uncaught exception → unhandled_error_handler → 500)

---

## Data Flow — Full Chat Request

```
POST /chat
    │
    ▼
chat_service.chat(db, req, user_id, api_key, minio, modelserver, langfuse)
    │
    ├── [1] modelserver_client.classify([req.message])
    │       └── POST http://modelserver:8001/classify
    │               body: {"texts": ["Why does iloc raise IndexError?"]}
    │               → {"labels": ["bug"]}
    │               label = "bug"
    │
    ├── [2] memory_service.get_relevant_memories(db, user_id, req.message, api_key)
    │       ├── openai_client.embed_one(req.message, api_key)
    │       │       → query_vec [1536-dim]
    │       └── memory_repo.search_by_similarity(db, user_id, query_vec, top_k=3)
    │               → ["How to fix SettingWithCopyWarning?", ...]
    │
    ├── [3] rag_service.search(db, SearchRequest(..., label="bug"), api_key, minio)
    │       ├── HyDE + hybrid search (full Phase 3 pipeline)
    │       └── → list[ChunkResult] (top-5 chunks with parent_text)
    │
    ├── [4] message_repo.list_by_conversation(db, req.conversation_id)
    │       └── → history (last 10 messages, ASC)
    │
    ├── [5] Build messages list
    │       system: _SYSTEM_PROMPT.format(label, memories, chunks)
    │       + history[-10:] messages
    │       + {"role": "user", "content": req.message}
    │
    ├── [6] Langfuse.trace("chat") → trace
    │       trace.generation("gpt-4o-mini") → generation
    │       AsyncOpenAI.chat.completions.create(
    │           model="gpt-4o-mini",
    │           messages=messages,
    │           max_tokens=512,
    │           temperature=0.3,
    │       )
    │       → reply: str
    │       generation.end(output=reply)
    │
    └── [7] Atomic DB commit:
            message_repo.create(db, conv_id, "user", req.message)
            message_repo.create(db, conv_id, "assistant", reply)
            memory_service.save_memory(db, user_id, req.message[:500], api_key)
                └── openai_client.embed_one(summary, api_key)
                    memory_repo.create(db, user_id, summary, embedding)
            db.commit()

    → ChatResponse(reply, label, sources)
```

---

## `api/config.py` Change

```python
# Added:
modelserver_host: str = "modelserver"
# Used in main.py: f"http://{settings.modelserver_host}:8001"
```

**Why `modelserver_host` not `modelserver_url`?**
Consistent with `db_host`, `redis_host`, `minio_host` — all settings are hostnames, not
full URLs. The port (8001) is fixed in the code since it's the internal Docker network
port and doesn't vary between environments.

**Why not in Vault?**
The modelserver hostname is topology (where the service lives), not a secret. Following
the same principle as db_host: topology goes in `.env`/`Settings`, secrets go in Vault.

---

## `api/main.py` Changes

```python
# New imports:
from app.api.routes.chat import router as chat_router
from app.infra.modelserver_client import ModelServerClient
from langfuse import Langfuse

# New in lifespan (after Vault secrets fetched):
app.state.modelserver_client = ModelServerClient(
    f"http://{settings.modelserver_host}:8001"
)
app.state.langfuse = Langfuse(
    public_key=secrets.langfuse_public_key,
    secret_key=secrets.langfuse_secret_key,
    host=secrets.langfuse_host,
)

# New in lifespan exit (before redis.aclose):
app.state.langfuse.flush()

# New router:
app.include_router(chat_router)   # prefix="/chat"
```

**Boot order in lifespan:**
```
configure_logging()
_check_eval_thresholds()     # exits if missing/zero
fetch_vault_secrets()        # exits if Vault unreachable
build_session_factory()      # DB connection pool
build_redis()                # Redis connection pool
build_minio()                # MinIO sync client
ModelServerClient()          # stateless HTTP client (no connection at construction)
Langfuse()                   # buffers events, sends async
yield                        # app serves requests
langfuse.flush()             # drain event buffer
redis.aclose()               # graceful Redis disconnect
```

---

## Architecture Decisions Made in This Phase

**D-classify-first: Why is classification before RAG, not after?**
The label is used as a `WHERE` filter in the RAG SQL query. It must be known before the
SQL runs. An alternative would be to run RAG without a filter and then re-rank by label
post-hoc — but this requires fetching more candidates and adds a re-ranking step. Filtering
at the DB level is more efficient and leverages the existing index.

**D-memory-raw: Why store the raw user message as memory, not a summary?**
Three reasons:
1. No extra LLM call (zero additional latency/cost)
2. User queries are already terse and information-dense — they embed well
3. The system is used for technical Q&A where the question IS the semantic content

The tradeoff: very long messages (>500 chars) are truncated. A proper summarization
approach would preserve the full semantic content. This is a known limitation for Phase 4.

**D-one-commit: Why one `db.commit()` for messages + memory?**
Transactional atomicity. The user message, assistant reply, and memory must all succeed
or all fail together. If we committed after the user message but the memory embedding
failed, the conversation history would be correct but memory would be incomplete.
Since all three write to different tables in the same session, one commit covers all.

**D-langfuse-state: Why store Langfuse client in app.state instead of creating per-request?**
`Langfuse()` initializes an async event queue and background sender. Creating it per-request
would spawn a new background thread on every chat call — memory leak and thread explosion.
`app.state.langfuse` is a single shared client. `flush()` on shutdown drains its queue.

**D-no-conversation-ownership-check: Known gap in Phase 4**
`chat_service` does not verify that `req.conversation_id` belongs to `user_id`. A user
could inject messages into another user's conversation. This is fixed in Phase 4-T
by adding `conversation_repo.get` at the start of `chat_service.chat` and raising
`PermissionDenied` if `conv.user_id != user_id`.

---

## Security

- OpenAI API key from `VaultSecrets.openai_api_key` — never hardcoded
- Langfuse keys (`langfuse_public_key`, `langfuse_secret_key`) from Vault
- modelserver called over internal Docker network only — port 8001 not exposed to host
- All LLM input (system prompt, history, user message) passes through structlog
  redaction before any log line is written — prevents accidental secret logging
- Memory summaries are user-scoped — no cross-user memory leakage
- GPT-4o-mini is called with `messages` typed as `list[dict[str, str]]` — no f-string
  injection into the prompt (user input is passed as a separate message dict)

**Prompt injection mitigation:**
User input is added as `{"role": "user", "content": req.message}` — a structured dict,
not concatenated into the system prompt string. The system prompt is rendered via
`.format()` with `label`, `memories_text`, and `chunks_text` which are either:
- Predicted strings from the modelserver (already sanitized by the classifier)
- Retrieved DB text (originally ingested by an admin)
Neither is raw user input. The user message lives in its own `role: user` slot.

---

## Acceptance Criteria (Phase 4-T)

### Unit tests (no Docker required)

- [ ] `pytest tests/test_phase4_chat_service.py -v`
  - `test_chat_calls_classify_with_user_message`
  - `test_chat_retrieves_memories_before_rag`
  - `test_chat_passes_label_to_rag_search`
  - `test_chat_persists_user_and_assistant_messages`
  - `test_chat_saves_memory_after_response`
  - `test_chat_commits_once`
  - `test_chat_returns_correct_reply_and_label`
  - `test_unknown_label_passes_none_to_rag`

- [ ] `pytest tests/test_phase4_memory.py -v`
  - `test_get_relevant_memories_returns_top_k_summaries`
  - `test_get_relevant_memories_returns_empty_when_no_memories`
  - `test_save_memory_embeds_and_stores`
  - `test_memory_is_user_scoped`

- [ ] `pytest tests/test_phase4_routes.py -v`
  - `test_chat_requires_auth`
  - `test_chat_returns_200_with_reply`
  - `test_chat_response_has_label_and_sources`

### Known issue to fix in Phase 4-T

- [ ] Add conversation ownership check in `chat_service.chat`:
  ```python
  conv = await conversation_repo.get(db, req.conversation_id)
  if not conv or conv.user_id != user_id:
      raise PermissionDenied("Conversation not found")
  ```

### Integration tests (requires Docker)

- [ ] Register → login → create conversation → chat → check messages table has 2 rows
- [ ] Second chat turn uses history from first turn in prompt
- [ ] After chat, memories table has a new row for the user
- [ ] Langfuse cloud shows a trace for the chat call
- [ ] modelserver 502 → chat returns 502 ToolFailure (not 500)
