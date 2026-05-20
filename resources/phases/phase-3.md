# Phase 3 — Advanced RAG Pipeline

## Summary

Phase 3 wires up the full retrieval-augmented generation pipeline: document
ingestion with parent-child chunking, hybrid dense+sparse search, HyDE query
transformation, and chunk snapshots written to MinIO.

---

## Files Created / Modified

### New files

| File | Purpose |
|------|---------|
| `api/app/infra/openai_client.py` | Async embedding wrapper (text-embedding-3-small) |
| `api/app/infra/modelserver_client.py` | HTTP client for modelserver /classify /rerank /ner |
| `api/app/infra/minio_client.py` | MinIO chunk snapshot upload |
| `api/app/services/chunker.py` | Parent-child hierarchical chunker |
| `api/app/repositories/chunk_repo.py` | bulk_insert + hybrid_search |
| `api/app/services/rag_service.py` | ingest, HyDE search, MinIO snapshot |
| `api/app/api/routes/rag.py` | POST /rag/ingest, POST /rag/search |

### Modified files

| File | Change |
|------|--------|
| `api/app/domain/models.py` | Added IngestRequest/Response, SearchRequest, ChunkResult |
| `api/main.py` | build_minio in lifespan, include rag_router |
| `api/requirements.txt` | Added tiktoken>=0.7.0 |

---

## Architecture Decisions

### Hierarchical chunking (chunker.py)

- **Parent**: ~1024 tokens — stored but NOT embedded; returned to the LLM as context window
- **Child**: ~256 tokens — embedded with text-embedding-3-small; used for retrieval
- Token counting uses tiktoken `cl100k_base` (same encoding as GPT-4 / text-embedding-3)
- `parent_id` FK on each child points to its parent; service fetches parent text before returning
- Both parent and child rows go into the `chunks` table with `chunk_type = 'parent'|'child'`

### Hybrid search (chunk_repo.py)

Formula: `score = 0.6 × dense_score + 0.4 × sparse_score`

- **Dense**: cosine similarity via pgvector HNSW index (`1 - (embedding <=> query_vec)`)
- **Sparse**: PostgreSQL FTS `ts_rank` on `search_vector` tsvector column (populated by DB trigger)
- Metadata filters (`label`, `source`) applied as `WHERE` conditions **before** HNSW scan
- Top-20 dense candidates fetched, then blended with sparse scores, final top-5 returned
- Raw SQL via SQLAlchemy `text()` — pgvector operator `<=>` not expressible in ORM

### HyDE (rag_service.py)

1. Embed original query → `query_vec`
2. Ask GPT-4o-mini to write a hypothetical GitHub issue that answers the query
3. Embed that answer → `hyp_vec`
4. `combined_vec = (query_vec + hyp_vec) / 2` (element-wise 50/50 blend)
5. Run hybrid search with `combined_vec`

If GPT-4o-mini call fails, falls back to original query (logged as warning, no crash).

### MinIO chunk snapshot (minio_client.py)

- Every `/rag/search` call uploads a JSON blob to MinIO at `chunk-snapshots/{conversation_id}/{uuid}.json`
- Upload is `asyncio.to_thread` around the synchronous `minio` SDK — keeps the event loop free
- Bucket created automatically on first write (`_ensure_bucket`)
- Provides audit trail: which context was shown for each conversation turn

### modelserver_client.py

Thin `httpx.AsyncClient` wrapper for the modelserver service (Phase 7):
- `POST /classify` → list of labels per text
- `POST /rerank` → cross-encoder scores per passage
- `POST /ner` → entity list for a text

Raises `ToolFailure` (502) on any HTTP error. Not wired into routes yet — used in Phase 4 chatbot.

---

## API Endpoints

### POST /rag/ingest (admin only)

```json
Request:  { "text": "...", "source": "pandas/issues", "label": "bug" }
Response: { "chunks_stored": 12 }
```

- Requires `Authorization: Bearer <admin-jwt>`
- Chunks text, embeds children, bulk-inserts, commits

### POST /rag/search (authenticated)

```json
Request:  { "query": "How to fix SettingWithCopyWarning?", "conversation_id": "<uuid>", "top_k": 5 }
Response: [{ "id": "...", "text": "...", "parent_text": "...", "label": "bug", "source": "pandas/issues", "score": 0.82 }]
```

- Requires `Authorization: Bearer <jwt>`
- HyDE + hybrid search + MinIO snapshot

---

## Security

- OpenAI API key consumed from `VaultSecrets.openai_api_key` — never in env vars or code
- MinIO credentials from `VaultSecrets.minio_access_key / minio_secret_key`
- Ingest is admin-only (`require_admin` dependency)
- Search returns only chunk metadata visible to any authenticated user (no ACL per chunk in Phase 3)

---

## Dependencies Added

| Package | Version | Reason |
|---------|---------|--------|
| tiktoken | >=0.7.0 | Token counting for parent/child splits |

(openai, minio, httpx, numpy already in requirements from Phase 1/2)
