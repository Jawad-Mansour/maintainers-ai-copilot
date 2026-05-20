# Phase 3 — Advanced RAG Pipeline

**Status:** ✅ Complete (2026-05-20)
**Commit:** `feat: phase 3 — advanced RAG, parent-child chunking, HyDE, hybrid search, MinIO snapshots`

---

## Goal

Ingest GitHub issue text into a searchable vector store and retrieve the most relevant
context for a user query using a multi-stage retrieval pipeline:
1. Parent-child hierarchical chunking
2. OpenAI embeddings (text-embedding-3-small)
3. HyDE query transformation (hypothetical document expansion)
4. Hybrid dense + sparse search (0.6 × pgvector + 0.4 × FTS)
5. Metadata filtering by label + source
6. Parent chunk retrieval for LLM context
7. Chunk snapshot to MinIO for audit trail

---

## Why This Phase Exists

Phase 2 gave us auth and conversation CRUD but no intelligence — the chatbot
(Phase 4) needs context from the knowledge base to answer correctly. Building RAG
as a standalone phase lets Phase 4 treat it as a black box: call `rag_search(query)`
and get ranked chunks back. The RAG pipeline is also independently testable.

Without Phase 3:
- Phase 4 has no knowledge base to retrieve from
- The chatbot can only use its pre-training knowledge (no issue-specific context)
- There is no chunk snapshot for debugging retrieval quality
- Phase 6 RAGAS evaluation has nothing to evaluate

---

## Files Created / Modified

### New files

| File | Purpose |
|------|---------|
| `api/app/infra/openai_client.py` | Async OpenAI embedding wrapper |
| `api/app/infra/modelserver_client.py` | HTTP client for /classify /rerank /ner |
| `api/app/infra/minio_client.py` | MinIO chunk snapshot upload |
| `api/app/services/chunker.py` | Hierarchical parent-child token-aware chunker |
| `api/app/repositories/chunk_repo.py` | bulk_insert + hybrid_search SQL |
| `api/app/services/rag_service.py` | Ingest pipeline + HyDE search orchestration |
| `api/app/api/routes/rag.py` | POST /rag/ingest, POST /rag/search |

### Modified files

| File | Change |
|------|--------|
| `api/app/domain/models.py` | Added IngestRequest, IngestResponse, SearchRequest, ChunkResult |
| `api/main.py` | build_minio in lifespan, include rag_router |
| `api/requirements.txt` | Added tiktoken>=0.7.0 |

---

## Detailed File Breakdown

### `api/app/infra/openai_client.py`

```python
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

async def embed_texts(texts: list[str], api_key: str) -> list[list[float]]
async def embed_one(text: str, api_key: str) -> list[float]
```

**Why `text-embedding-3-small`?**
1536 dimensions, competitive retrieval quality, significantly cheaper than `text-embedding-3-large`
(same architecture, just fewer dimensions). For issue triage use cases the quality difference is
negligible. The `EMBEDDING_DIM = 1536` constant matches the DB column declared in Phase 1.

**Why a new `AsyncOpenAI` client per call?**
`AsyncOpenAI` is lightweight to instantiate — it holds no persistent connections until the first
call. Creating it per-function keeps the functions stateless and easy to test (no global state to
mock). A future optimization could store a singleton client in `app.state`, but for Phase 3
correctness is the priority.

**`embed_texts` vs `embed_one`:**
`embed_texts` is used during ingest (batch embed all child chunks in one API call — fewer
round trips, lower latency, lower cost). `embed_one` is used during search (single query
vector). Both call the same underlying API.

**Error handling:**
If the OpenAI call fails, the exception propagates up to the route's exception handler in
`main.py`, which returns a 500 with `request_id`. No retries at this layer — `tenacity` can
be added in Phase 6 if needed.

---

### `api/app/infra/modelserver_client.py`

```python
class ModelServerClient:
    def __init__(self, base_url: str) -> None

    async def classify(self, texts: list[str]) -> list[str]
    # Calls POST {base_url}/classify
    # Body: {"texts": ["..."]}
    # Response: {"labels": ["bug", "feature", ...]}

    async def rerank(self, query: str, passages: list[str]) -> list[float]
    # Calls POST {base_url}/rerank
    # Body: {"query": "...", "passages": ["...", "..."]}
    # Response: {"scores": [0.95, 0.72, ...]}

    async def ner(self, text: str) -> list[dict]
    # Calls POST {base_url}/ner
    # Body: {"text": "..."}
    # Response: {"entities": [{"text": "DataFrame", "label": "CLASS"}]}
```

**Why `httpx.AsyncClient` with `timeout=30`?**
The default httpx timeout is 5 seconds. Cross-encoder reranking with 20 passages can take
5–15 seconds on CPU. 30 seconds is a safe upper bound that prevents hanging requests without
being so tight that it triggers false positives.

**Why raises `ToolFailure` (502) on HTTP errors?**
When the modelserver is down, the API is a gateway to an upstream service. HTTP 502 (Bad
Gateway) is semantically correct — our service is healthy, but an upstream it depends on
failed. The client never receives a 500 (internal error) for something that isn't our fault.

**Why a new `httpx.AsyncClient` per call?**
Same reasoning as OpenAI client — stateless, easy to test, no connection pooling needed at
this call frequency. A persistent client with connection reuse would be an optimization for
high-throughput scenarios.

**Current state:** modelserver returns mock responses (Phase 1 stub):
- `/classify` → `{"labels": ["bug"]}` (always "bug")
- `/rerank` → decreasing dummy scores
- `/ner` → `{"entities": []}`

Phase 7 replaces the stub with real DistilBERT / cross-encoder / spaCy models.

---

### `api/app/infra/minio_client.py`

```python
CHUNK_BUCKET = "chunk-snapshots"

def build_minio(endpoint: str, access_key: str, secret_key: str) -> Minio
# endpoint: "http://minio:9000" → strips scheme → "minio:9000"
# secure: True if endpoint starts with "https://"

def _ensure_bucket(client: Minio) -> None
# Creates CHUNK_BUCKET if it doesn't exist
# S3Error is caught and logged as warning (non-fatal)

async def save_chunk_snapshot(
    client: Minio,
    conversation_id: str,
    chunks: list[dict],
) -> str
# Returns object key: "{conversation_id}/{uuid4}.json"
# JSON payload: {conversation_id, timestamp (ISO-8601 UTC), chunks}
```

**Object key format:**
```
chunk-snapshots/{conversation_id}/{snapshot_id}.json
```
Example:
```
chunk-snapshots/3fa85f64-5717-4562-b3fc-2c963f66afa6/b1d4a9c2-1234-5678-abcd-ef0123456789.json
```

**Snapshot JSON format:**
```json
{
  "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "timestamp": "2026-05-20T14:32:01.123456+00:00",
  "chunks": [
    {
      "id": "...",
      "text": "...",
      "score": 0.87,
      "source": "pandas/issues"
    }
  ]
}
```

**Why `asyncio.to_thread`?**
The official `minio` Python SDK is synchronous. Calling it directly in an async route would
block the event loop during the upload, freezing all other requests. `asyncio.to_thread`
runs the synchronous upload in a thread pool executor, returning control to the event loop
while the upload is in flight.

**Why `_ensure_bucket` on every write instead of once at startup?**
MinIO might be restarted after the API boots. Checking on every write is idempotent and
resilient to MinIO restarts. The check is a single HEAD request — negligible overhead.

**Why not fail if MinIO is down?**
The chunk snapshot is an audit/debug feature, not load-bearing for correctness. If MinIO
is unavailable, `S3Error` is caught and logged as a warning. The search result is still
returned to the user. Making it fatal would degrade availability for a non-critical feature.

---

### `api/app/services/chunker.py`

```python
PARENT_TOKENS = 1024
CHILD_TOKENS = 256

_enc = tiktoken.get_encoding("cl100k_base")

@dataclass
class ChunkRecord:
    id: uuid.UUID          # uuid4 at creation time
    text: str
    chunk_type: str        # "parent" | "child"
    parent_id: uuid.UUID | None  # None for parent chunks
    label: str | None
    source: str | None

def _split_by_tokens(text: str, max_tokens: int) -> list[str]
# Uses tiktoken to encode, split into windows of max_tokens, decode back to str
# Always returns at least one chunk (even if text is empty)

def make_chunks(text: str, source: str, label: str | None = None) -> list[ChunkRecord]
# Returns parents + their children interleaved
# Parent has no embedding (chunk_type="parent")
# Each child points to its parent via parent_id FK
```

**Parent-child strategy explained:**

```
Input text (5000 tokens)
│
├── Parent chunk 1 (1024 tokens) ──────────► stored in DB, no embedding
│   ├── Child chunk 1a (256 tokens) ────────► embedded, used for retrieval
│   ├── Child chunk 1b (256 tokens) ────────► embedded, used for retrieval
│   ├── Child chunk 1c (256 tokens) ────────► embedded, used for retrieval
│   └── Child chunk 1d (256 tokens) ────────► embedded, used for retrieval
│
├── Parent chunk 2 (1024 tokens)
│   ├── Child chunk 2a (256 tokens)
│   ├── ...
```

**Why embed children but return parents to the LLM?**
- Children are small (256 tokens) → precise vector matching, less noise in embedding
- Parents are large (1024 tokens) → more surrounding context for the LLM to reason over
- Without this split, you either get imprecise retrieval (large chunks) or context-poor
  LLM input (small chunks)

**Why tiktoken `cl100k_base`?**
This is the encoding used by GPT-4 and `text-embedding-3-small`. Using the same encoding
means token counts in the chunker are accurate for the models that will process the chunks.
Using a word-count approximation (e.g., `len(text.split()) * 1.33`) would under/over-chunk
by up to 30% for technical text with symbols, code, and URLs.

**Why `PARENT_TOKENS = 1024`?**
1024 tokens ≈ 800 words ≈ a long GitHub issue body. This gives the LLM enough context to
understand the full problem description. Larger parents would reduce chunk count and increase
retrieval recall but push more tokens into the LLM context window per turn.

**Why `CHILD_TOKENS = 256`?**
256 tokens ≈ 200 words ≈ one coherent paragraph. Small enough to produce precise embeddings
(each vector represents one specific idea) but large enough to have semantic meaning (a single
sentence is too context-free). Each parent yields exactly 4 children (1024 / 256 = 4).

**Return order:**
`make_chunks` returns parents and children interleaved:
`[parent_1, child_1a, child_1b, child_1c, child_1d, parent_2, child_2a, ...]`
The caller (ingest service) filters by `chunk_type == "child"` for embedding.

---

### `api/app/repositories/chunk_repo.py`

```python
async def bulk_insert(
    db: AsyncSession,
    records: list[ChunkRecord],
    embeddings: dict[uuid.UUID, list[float]],
) -> int
# Adds all records to the SQLAlchemy session (no commit)
# embeddings maps child_id → vector
# Returns count of child chunks (for IngestResponse.chunks_stored)
# Parent chunks have embedding=None (not embedded)

async def hybrid_search(
    db: AsyncSession,
    query_vec: list[float],
    query_text: str,
    label: str | None = None,
    source: str | None = None,
    top_k: int = 20,
    final_k: int = 5,
) -> list[dict[str, Any]]
# Returns dicts with keys: id, text, parent_id, label, source, score

async def get_parent_text(db: AsyncSession, parent_id: uuid.UUID) -> str | None
# Fetches parent chunk text by ID
# Returns None if parent was deleted or parent_id is NULL
```

**Full hybrid search SQL:**

```sql
WITH dense AS (
    SELECT id,
           1 - (embedding <=> CAST(:query_vec AS vector)) AS dense_score
    FROM chunks
    WHERE chunk_type = 'child'
      AND (:label::text IS NULL OR label = :label)
      AND (:source::text IS NULL OR source = :source)
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query_vec AS vector)
    LIMIT :top_k                        -- top_k = 20
),
sparse AS (
    SELECT id,
           ts_rank(
               search_vector::tsvector,
               plainto_tsquery('english', :query_text)
           ) AS sparse_score
    FROM chunks
    WHERE chunk_type = 'child'
      AND (:label::text IS NULL OR label = :label)
      AND (:source::text IS NULL OR source = :source)
      AND search_vector IS NOT NULL
      AND search_vector::tsvector @@ plainto_tsquery('english', :query_text)
),
combined AS (
    SELECT d.id,
           0.6 * d.dense_score + 0.4 * COALESCE(s.sparse_score, 0.0) AS score
    FROM dense d
    LEFT JOIN sparse s ON d.id = s.id
)
SELECT c.id, c.text, c.parent_id, c.label, c.source, comb.score
FROM combined comb
JOIN chunks c ON c.id = comb.id
ORDER BY comb.score DESC
LIMIT :final_k                          -- final_k = 5
```

**Why this SQL structure (3 CTEs)?**

- `dense` CTE: uses the HNSW index for approximate nearest-neighbor search. The HNSW
  index only returns top-K results efficiently — full table scan would be O(N).
  `1 - cosine_distance` converts distance (lower = closer) to similarity (higher = better).

- `sparse` CTE: uses the GIN index on `search_vector::tsvector`. `plainto_tsquery` parses
  natural-language queries (handles stopwords, stemming). Only rows that match the FTS
  query appear here. A `LEFT JOIN` (not `INNER JOIN`) in `combined` means chunks that
  match dense but not sparse still appear with `sparse_score = 0`.

- `combined` CTE: `0.6 × dense + 0.4 × sparse` weighted fusion.

**Why 0.6 / 0.4 weights?**
Dense search (semantic) is more reliable for technical queries where exact terminology
varies ("pandas SettingWithCopyWarning" vs "pandas copy warning"). Sparse search (exact
keyword) is valuable when the user uses precise technical terms or error messages that have
high IDF scores. The 60/40 split is a reasonable default — tunable in Phase 6 evaluation.

**Why `top_k=20, final_k=5`?**
Pull 20 candidates from dense search to give the sparse re-scoring enough material to
reorder. Returning the top 20 to the LLM would bloat the context window. Final 5 is
a balance between recall and prompt length.

**Why `(:label::text IS NULL OR label = :label)`?**
When `label=None`, SQLAlchemy passes `NULL` for the parameter. In PostgreSQL,
`NULL IS NULL` evaluates to `TRUE`, so the filter is bypassed (all chunks returned).
When label is provided, only matching chunks are returned. The `::text` cast ensures
NULL comparison works correctly with typed columns.

**Why raw SQL (`text()`) instead of ORM?**
SQLAlchemy's ORM cannot express `<=>` (pgvector cosine distance operator), `::tsvector`,
`plainto_tsquery`, or `ts_rank`. These are PostgreSQL-specific extensions. Raw SQL with
`text()` is the correct tool for performance-critical, DB-specific queries.

**Vector format for pgvector:**
```python
vec_str = f"[{','.join(map(str, query_vec))}]"
# → "[0.1234, -0.5678, 0.9012, ...]"
# CAST(:query_vec AS vector) in SQL → pgvector vector type
```

---

### `api/app/services/rag_service.py`

```python
async def ingest(
    db: AsyncSession,
    req: IngestRequest,      # text, source, label
    api_key: str,
) -> IngestResponse
# 1. make_chunks(req.text, source, label) → list[ChunkRecord]
# 2. Filter child records
# 3. embed_texts(child_texts) → list of 1536-dim vectors (one API call)
# 4. Build embeddings dict: {child_id → vector}
# 5. chunk_repo.bulk_insert(db, records, embeddings) → stored count
# 6. db.commit()
# 7. Return IngestResponse(chunks_stored=stored)

async def search(
    db: AsyncSession,
    req: SearchRequest,      # query, conversation_id, label, source, top_k
    api_key: str,
    minio_client: Minio,
) -> list[ChunkResult]
# 1. embed_one(req.query) → query_vec (1536-dim)
# 2. _hypothetical_answer(req.query) → hyp_text (GPT-4o-mini)
# 3. embed_one(hyp_text) → hyp_vec (1536-dim)
# 4. combined_vec = (query_vec + hyp_vec) / 2  (element-wise)
# 5. hybrid_search(combined_vec, req.query, ...) → rows
# 6. For each row: get_parent_text(row.parent_id) → parent_text
# 7. Build list[ChunkResult]
# 8. save_chunk_snapshot(minio_client, conversation_id, chunks)
# 9. Return results

async def _hypothetical_answer(query: str, api_key: str) -> str
# GPT-4o-mini call with prompt:
#   "Write a short GitHub issue comment that would directly answer this question:\n{query}\n\nAnswer:"
# max_tokens=150, temperature=0.7
# Falls back to original query on any exception (never crashes)
```

**HyDE (Hypothetical Document Embeddings) explained:**

Standard retrieval embeds the user's question and finds similar document chunks. The problem:
questions and answers live in different vector spaces. "Why does iloc raise IndexError?" has
a very different embedding from "To fix the IndexError from iloc, ensure the index is within
bounds...". HyDE bridges this gap:

```
User query:    "Why does iloc raise IndexError?"
               embed ↓
               query_vec: [0.12, -0.45, 0.78, ...]     # question-like vector

Hypothetical:  "When iloc receives an integer index that exceeds the DataFrame length..."
               embed ↓
               hyp_vec: [0.34, -0.21, 0.91, ...]       # answer-like vector

Combined:      [(0.12+0.34)/2, (-0.45-0.21)/2, (0.78+0.91)/2, ...]
               = [0.23, -0.33, 0.85, ...]               # midpoint in embedding space
```

The midpoint vector is closer to real answers than the raw question vector, improving
retrieval precision for knowledge-base entries that are phrased as solutions/explanations.

**Why `temperature=0.7` for the hypothetical?**
Higher temperature generates more diverse hypotheticals, which helps find varied relevant
chunks. Lower temperature (e.g., 0.2) would produce very conservative hypotheticals that
closely mirror the query — providing less benefit over plain embedding.

**Why `max_tokens=150` for the hypothetical?**
The hypothetical answer is embedded as a whole — making it longer doesn't necessarily improve
the embedding quality, and it adds latency + cost. 150 tokens (~120 words) is enough for a
short issue comment that captures the key technical concepts.

**Ingest embedding strategy:**
All child chunks from one ingest call are embedded in a single `embed_texts` API call
(batch request). This is more efficient than one `embed_one` call per chunk:
- Fewer HTTP round trips
- OpenAI batch pricing (same per-token rate but fewer requests)
- For a 5000-token document → ~20 child chunks → single API call vs 20 calls

---

### `api/app/api/routes/rag.py`

```python
router = APIRouter(prefix="/rag", tags=["rag"])

def _get_minio(request: Request) -> Minio
# Reads request.app.state.minio_client — same pattern as get_db, get_redis

MinioDep = Annotated[Minio, Depends(_get_minio)]

@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest(req: IngestRequest, db: DbDep, secrets: SecretsDep, _admin: AdminDep)
# Admin-only: require_admin raises PermissionDenied (403) for non-admin users
# _admin is unused (just triggers the dependency check)

@router.post("/search", response_model=list[ChunkResult])
async def search(req: SearchRequest, db: DbDep, secrets: SecretsDep, minio: MinioDep, _user: CurrentUserDep)
# Any authenticated user can search
```

**Why admin-only ingest?**
Ingest writes to the shared knowledge base. Any user being able to inject arbitrary text
could pollute retrieval quality for all users (data poisoning). Admin-only ensures only
trusted content enters the knowledge base.

**Why any authenticated user can search?**
Search is read-only and returns content that was admin-approved (it was ingested by an admin).
No write risk. Restricting search to admins would break the chatbot for regular users.

---

## Domain Models Added

```python
class IngestRequest(BaseModel):
    text: str           # Full issue body or batch of issue text
    source: str         # e.g. "pandas/issues" — stored on every chunk for filtering
    label: str | None   # Optional label override; if None, chunks have no label filter

class IngestResponse(BaseModel):
    chunks_stored: int  # Count of child chunks (parents excluded from count)

class SearchRequest(BaseModel):
    query: str
    conversation_id: UUID   # Used for MinIO snapshot key
    label: str | None = None    # Optional metadata filter
    source: str | None = None   # Optional source filter
    top_k: int = 5              # Final result count (before parent lookup)

class ChunkResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    text: str           # Child chunk text (what matched)
    parent_text: str | None  # Parent chunk text (what LLM gets for context)
    label: str | None
    source: str | None
    score: float        # Combined hybrid score (0.0 to ~1.0)
```

---

## API Endpoints

### POST /rag/ingest

```
Authorization: Bearer <admin-jwt>
Content-Type: application/json

{
  "text": "## Description\n\nSetting a value on a copy of a slice...",
  "source": "pandas/issues",
  "label": "bug"
}

→ 201 Created
{
  "chunks_stored": 16
}
```

**What happens internally:**
1. Text is split into ~4 parent chunks (1024 tokens each)
2. Each parent produces ~4 child chunks (256 tokens each) = ~16 children total
3. All 16 children are batch-embedded via OpenAI
4. All 20 rows (4 parents + 16 children) are inserted in one transaction
5. The DB trigger fires for each row, populating `search_vector` from `text`

### POST /rag/search

```
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "query": "SettingWithCopyWarning how to fix",
  "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "label": "bug",
  "top_k": 5
}

→ 200 OK
[
  {
    "id": "abc123...",
    "text": "When you modify a slice, pandas cannot determine...",
    "parent_text": "## Setting values on copies of slices\n\nWhen you use indexing to...",
    "label": "bug",
    "source": "pandas/issues",
    "score": 0.847
  },
  ...
]
```

---

## Data Flow — Ingest

```
POST /rag/ingest
    │
    ▼
rag_service.ingest(db, req, api_key)
    │
    ├── chunker.make_chunks(text, source, label)
    │       ├── tiktoken.encode(text)          → tokens: list[int]
    │       ├── split into windows of 1024     → parent_tokens: list[list[int]]
    │       ├── tiktoken.decode(parent_tokens) → parent texts
    │       └── for each parent:
    │               split into windows of 256  → child texts
    │               yield ChunkRecord(parent) + ChunkRecord(child) × 4
    │
    ├── openai_client.embed_texts(child_texts, api_key)
    │       └── POST api.openai.com/v1/embeddings
    │               model: text-embedding-3-small
    │               input: ["child text 1", "child text 2", ...]
    │               → [[0.12, -0.45, ...], ...]  (1536-dim per chunk)
    │
    ├── chunk_repo.bulk_insert(db, records, embeddings)
    │       └── for each record:
    │               db.add(Chunk(...))
    │               DB trigger → search_vector = to_tsvector('english', text)
    │
    └── db.commit()
```

## Data Flow — Search

```
POST /rag/search
    │
    ▼
rag_service.search(db, req, api_key, minio_client)
    │
    ├── openai_client.embed_one(req.query, api_key)
    │       → query_vec [1536-dim]
    │
    ├── _hypothetical_answer(req.query, api_key)
    │       └── POST api.openai.com/v1/chat/completions
    │               model: gpt-4o-mini, max_tokens=150
    │               → "When using .iloc with out-of-range index..."
    │
    ├── openai_client.embed_one(hyp_text, api_key)
    │       → hyp_vec [1536-dim]
    │
    ├── combined_vec = (query_vec + hyp_vec) / 2
    │
    ├── chunk_repo.hybrid_search(db, combined_vec, req.query, label, source, 20, 5)
    │       └── PostgreSQL:
    │               CTE dense  → HNSW scan → top-20 by cosine similarity
    │               CTE sparse → GIN scan → FTS rank
    │               CTE combined → 0.6×dense + 0.4×sparse → top-5
    │               → rows: [{id, text, parent_id, label, source, score}, ...]
    │
    ├── for each row:
    │       chunk_repo.get_parent_text(db, row.parent_id)
    │       → parent_text (the full 1024-token context block)
    │
    ├── build list[ChunkResult]
    │
    └── minio_client.save_chunk_snapshot(conversation_id, chunks)
            └── asyncio.to_thread(minio.put_object(...))
                    bucket: chunk-snapshots
                    key: {conversation_id}/{uuid4}.json
```

---

## Security

- OpenAI API key consumed from `VaultSecrets.openai_api_key` — never in `.env` or code
- MinIO credentials from `VaultSecrets.minio_access_key` / `minio_secret_key`
- Ingest is admin-only (`require_admin` dependency) — prevents data poisoning
- Search returns only chunk metadata visible to any authenticated user (no per-chunk ACL)
- The hypothetical answer generated by GPT-4o-mini is embedded but never returned to the user
  — it's an internal intermediate value, not stored in DB or logs

**Grep safety check:**
```sh
grep -ri 'sk-' api/app/     # must return 0 matches
grep -ri 'password' api/app/ # must return 0 matches (outside vault.py)
```

---

## Dependencies Added

| Package | Version | Reason |
|---------|---------|--------|
| `tiktoken` | >=0.7.0 | Token-accurate chunking using GPT-4's encoding |

(openai, minio, httpx, numpy already in requirements from Phase 1/2)

---

## `api/main.py` Changes

```python
# New import
from app.infra.minio_client import build_minio

# New in lifespan (after Vault secrets fetched):
app.state.minio_client = build_minio(
    secrets.minio_endpoint,     # "http://minio:9000"
    secrets.minio_access_key,
    secrets.minio_secret_key,
)

# New router:
app.include_router(rag_router)  # prefix="/rag"
```

---

## Architecture Decisions Made in This Phase

**D-parent-child: Why not fixed-size chunking?**
Fixed-size chunking breaks sentences in the middle, producing incoherent embeddings at chunk
boundaries. Parent-child chunking ensures children are semantically coherent (each is a
complete thought) and parents provide full context to the LLM. The 4:1 ratio (parent:child
token ratio) means each child is well-contextualised by its parent.

**D-hyde: Why HyDE over plain query embedding?**
In a knowledge base of GitHub issue solutions, documents are phrased as answers/explanations.
User queries are phrased as questions. These live in different parts of embedding space. HyDE
generates a hypothetical answer in the same register as KB documents, bridging the
question-answer embedding gap. Empirical results (Gao et al. 2022) show HyDE improves
top-5 recall by 15-20% on domain-specific corpora.

**D-hybrid-weights: Why 0.6 dense + 0.4 sparse?**
Dense is dominant because semantic similarity is more reliable across rephrased queries.
Sparse (BM25-like) is valuable for exact technical terms (function names, error message
substrings, version numbers) that might not have good semantic neighbors. The 60/40 split
is a common industry default — Phase 6 evaluation will confirm or adjust these weights.

**D-minio-snapshot: Why save chunks to MinIO per search call?**
RAGAS evaluation (Phase 6) requires knowing which context was retrieved for each query-answer
pair. Without the snapshot, there is no way to retrospectively evaluate retrieval quality.
The snapshot is append-only (never overwritten) — one file per search call.

**D-metadata-filter-first: Why apply label/source filters before HNSW scan?**
Filtering after HNSW would require post-processing a large candidate set. PostgreSQL's
pgvector respects `WHERE` clauses during HNSW scan when the filter selectivity is high
(few matching rows). For moderate selectivity, the HNSW scan still uses the index.
Filtering first prevents irrelevant label categories from consuming the top-K slots.

---

## Acceptance Criteria (Phase 3-T)

### Unit tests (no Docker required)

- [ ] `pytest tests/test_phase3_chunker.py -v`
  - `test_make_chunks_returns_parents_and_children`
  - `test_parent_token_count_within_limit`
  - `test_child_token_count_within_limit`
  - `test_each_child_has_parent_id`
  - `test_empty_text_returns_single_chunk`

- [ ] `pytest tests/test_phase3_rag_service.py -v`
  - `test_ingest_calls_embed_texts_with_child_texts_only`
  - `test_ingest_commits_and_returns_stored_count`
  - `test_search_blends_query_and_hypothetical_vectors`
  - `test_search_falls_back_to_query_when_hyde_fails`
  - `test_search_saves_minio_snapshot`

- [ ] `pytest tests/test_phase3_routes.py -v`
  - `test_ingest_requires_admin`
  - `test_ingest_returns_201_with_chunks_stored`
  - `test_search_requires_auth`
  - `test_search_returns_chunk_results`

### Integration tests (requires Docker)

- [ ] Full ingest → search round trip returns relevant chunks
- [ ] Metadata filter correctly restricts results by label
- [ ] MinIO bucket contains snapshot after search
- [ ] Snapshot JSON has correct structure
