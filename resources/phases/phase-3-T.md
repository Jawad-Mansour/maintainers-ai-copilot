# Phase 3-T — Advanced RAG Pipeline Tests

**Status:** ✅ All passed (2026-05-20)
**Scope:** 3 test files, 20 tests — chunker, RAG service, and HTTP routes.

---

## Goal

Verify that the hierarchical chunker produces correctly structured output, the RAG search
pipeline connects HyDE, hybrid retrieval, and cross-encoder reranking in the right order,
and the HTTP endpoints enforce authentication and admin authorization. These tests run
without a database or real API keys — every external call is mocked, letting us test
the orchestration logic independently of network conditions.

---

## Why Separate Tests for Each Pipeline Layer

The Phase 3 RAG pipeline has three distinct layers with different responsibilities:

1. **Chunker** (`chunker.py`) — pure Python, no I/O. Takes text, returns a list of
   `ChunkRecord` objects. If this is wrong, everything downstream gets bad data.

2. **RAG service** (`rag_service.py`) — orchestrates: embed → HyDE → hybrid search →
   parent lookup → rerank → MinIO snapshot. If the orchestration is wrong, the right
   chunks may be available in the database but returned in the wrong order, cut off
   at the wrong threshold, or served without their parent context.

3. **Routes** (`rag.py`) — HTTP layer. Enforces that ingest requires admin and search
   requires authentication. If this is wrong, unauthenticated users can query the
   knowledge base or non-admins can write to it.

Testing all three in the same test file would make failures ambiguous. The layered
structure mirrors the code structure.

---

## Test Files

### `tests/test_phase3_chunker.py` — 11 tests

The chunker is the entry point to the RAG pipeline. It converts raw text into a
two-level hierarchy: parent chunks (1024 tokens, served to the LLM as context) and
child chunks (256 tokens, embedded and indexed in pgvector). This file tests the
structural guarantees of that hierarchy.

**`test_short_text_yields_one_parent`** — Feeds a short string (< 256 tokens) to
`make_chunks()`. Asserts exactly one record has `chunk_type == "parent"`. Short text
fits in a single parent — there's no reason to split it.

**`test_short_text_yields_at_least_one_child`** — Same input. Asserts at least one
`chunk_type == "child"` exists. Every parent must have at least one child, because
only children are embedded — a parent with no children would be unreachable through
vector search.

**`test_child_references_parent`** — For a short text producing one parent and one
child, asserts that `child.parent_id == parent.id`. The parent-child link is what
enables parent retrieval: when the vector search returns a child, the service looks up
its `parent_id` to fetch the larger context for the LLM prompt.

**`test_source_propagated_to_all_chunks`** — Calls `make_chunks(text, source="gh://x/y#1")`.
Asserts that every returned record (parent and child) has `source == "gh://x/y#1"`.
The source field is displayed in the chat response as a citation. If it's not propagated
to all chunks, the citation would be missing.

**`test_label_propagated_to_all_chunks`** — Same test for the `label` field (e.g.,
`"bug"`, `"feature"`). The label is used by the metadata filter in hybrid search.
If a child's label is missing, the metadata filter can't restrict the search to
relevant issue types.

**`test_label_none_when_not_provided`** — Calls `make_chunks()` without a `label`
argument. Asserts all records have `label is None`. Confirms the label field is optional
and not defaulted to a wrong value.

**`test_chunk_records_have_unique_ids`** — For any text input, collects all record IDs
and asserts they form a set with no duplicates. If two chunks share an ID (e.g., because
of a copy-paste bug in the ID generation), one would overwrite the other in the database
during bulk insert.

**`test_chunk_records_are_chunk_record_instances`** — Asserts that every returned object
is an instance of `ChunkRecord` (the dataclass defined in `chunker.py`). Guards against
accidental type changes (e.g., if someone refactored the return type to a dict).

**`test_long_text_produces_multiple_parents`** — Feeds a text that is definitely longer
than 1024 tokens (generated with enough words to span multiple windows). Asserts more
than one parent is produced. Confirms that the sliding window logic actually splits long
texts rather than cramming everything into one oversized parent.

**`test_parent_children_all_reference_correct_parent`** — For a long text with multiple
parents, groups children by `parent_id` and asserts each parent ID in the children set
actually corresponds to an existing parent record. Catches the case where child
`parent_id` values are stale or incorrectly assigned during multi-parent chunking.

**`test_child_text_is_substring_of_parent`** — For each child, asserts that `child.text`
appears somewhere in its parent's `text` (or that the child text tokens are a subset of
the parent text tokens). The parent-child relationship is hierarchical — the child is a
portion of the parent. If child text contains tokens outside its parent, the retrieval
explanation ("relevant chunk in context of parent") would be misleading.

---

### `tests/test_phase3_rag_service.py` — 5 tests

This file tests the RAG service orchestration with all external calls mocked: OpenAI
embeddings, the modelserver reranker, the chunk repository, and MinIO. The goal is to
verify that the service calls them in the correct order with correct arguments.

**`test_ingest_stores_correct_count`** — Mocks `embed_texts` (returns a list of fake
vectors) and `chunk_repo.bulk_insert` (returns an integer count). Calls `ingest()` with
a short text. Asserts the returned `IngestResponse.chunks_stored` equals the mocked
integer. Confirms the ingest function correctly passes embedding results to the repo
and surfaces the count to the caller.

**`test_search_calls_reranker`** — Mocks `embed_one`, `_hypothetical_answer`,
`chunk_repo.hybrid_search`, and `modelserver_client.rerank`. Calls `search()`. Asserts
`modelserver_client.rerank` was called once. This is the critical correctness check:
the pipeline must call the reranker on every search, not bypass it when results are
present.

**`test_search_rerank_sorts_by_score`** — Mocks the reranker to return scores
`[0.1, 0.9, 0.5]` for a list of three chunks. After the call, asserts that the returned
chunks are in descending score order (the chunk that was scored 0.9 is first). This
catches bugs in the sort direction (ascending vs. descending) or in the zip-then-sort
logic.

**`test_search_hybrid_uses_20_candidates`** — Asserts that `chunk_repo.hybrid_search`
is called with `top_k=20`. The design specifies that the pipeline fetches 20 candidates
for cross-encoder reranking and then slices to `req.top_k` (usually 5) after. If
someone accidentally passes `top_k=5` to the DB query, the reranker only sees 5
candidates instead of 20, losing recall. This test locks in the 20-candidate contract.

**`test_search_empty_results_skips_rerank`** — Mocks `chunk_repo.hybrid_search` to
return an empty list. Asserts that `modelserver_client.rerank` is NOT called. When
there are no results to rerank, calling the reranker with an empty list would either
error or return a confusing empty-scores list. Skipping it entirely is the correct
behavior.

---

### `tests/test_phase3_routes.py` — 4 tests

These are HTTP-level tests using FastAPI's `ASGITransport` client. Two `client` fixtures
are defined: `client` (authenticated regular user) and `admin_client` (admin user). Both
override `get_db`, `get_current_user`, `require_admin`, and the infra clients stored on
`app.state`.

**`test_ingest_returns_201`** — POSTs to `/rag/ingest` with a valid payload as an admin
user. Mocks `rag_service.ingest` to return `IngestResponse(chunks_stored=5)`. Asserts
HTTP 201 and `response.json()["chunks_stored"] == 5`.

**`test_ingest_requires_admin`** — Overrides `require_admin` dependency to raise
`PermissionDenied`. Asserts that `/rag/ingest` returns HTTP 403. This is the authorization
boundary test: ingest writes to the knowledge base, so it must be admin-only. A regular
authenticated user must not be able to inject content.

**`test_search_returns_200`** — POSTs to `/rag/search` with a regular user (no admin
required for search). Mocks `rag_service.search` to return one `ChunkResult`. Asserts
HTTP 200, a list response with length 1, and that `data[0]["source"]` matches.

**`test_search_requires_auth`** — Overrides `get_current_user` to raise
`AuthenticationError`. Asserts HTTP 401. Confirms that unauthenticated requests cannot
query the knowledge base at all — the endpoint is not public.

---

## Test Design: Mocking the Full RAG Stack

The RAG service calls five external services: OpenAI (embeddings), the modelserver
(HyDE generation + reranking), PostgreSQL (hybrid search), MinIO (snapshot upload),
and `chunk_repo.get_parent_text` (parent lookup). Every one of these is mocked in the
service tests using `unittest.mock.patch` or `unittest.mock.AsyncMock`.

The key insight is that we are not testing whether OpenAI returns good embeddings or
whether pgvector finds the right chunks — we are testing whether the **orchestration**
is correct. Does the service call `embed_one` before calling `hybrid_search`? Does it
call `rerank` with the right query and chunk texts? Does it slice the results to
`req.top_k` after reranking, not before?

These are code-structure questions, not data-quality questions. They are best answered
by mocks, not by integration tests that require a running database and API keys.

---

## Issues Hit During Phase 3-T

### `B905` — `zip()` without `strict=True`

Ruff's `B905` rule requires all `zip()` calls to explicitly set `strict=True` or
`strict=False`. The reranking line:
```python
r for r, _ in sorted(zip(results, scores), key=lambda x: x[1], reverse=True)
```
was flagged because `zip()` without `strict=True` silently truncates if the two lists
have different lengths. If `rerank()` returns fewer scores than there are results (e.g.,
due to a modelserver bug), the trailing results would be silently dropped.

**Fix:** Added `strict=True` and extracted the sorted expression:
```python
paired = sorted(zip(results, scores, strict=True), key=lambda x: x[1], reverse=True)
results = [r for r, _ in paired]
```
With `strict=True`, a length mismatch raises `ValueError` immediately, making the bug
visible instead of hidden.

### `E501` — Long fixture signatures

`test_phase3_routes.py` had fixture signatures exceeding 100 characters:
```python
async def client(fake_secrets, fake_user, fake_admin, mock_db, mock_redis, mock_minio, mock_modelserver):
```
Fixed by splitting across lines:
```python
async def client(
    fake_secrets, fake_user, fake_admin, mock_db, mock_redis, mock_minio, mock_modelserver
):
```

---

## Pass Criteria — All Met ✅

- [x] `pytest tests/test_phase3_chunker.py -v` → **11 passed**
- [x] `pytest tests/test_phase3_rag_service.py -v` → **5 passed**
- [x] `pytest tests/test_phase3_routes.py -v` → **4 passed**
- [x] **Total: 20/20 passed**

**Phase 3-T passed. Cleared to proceed to Phase 4.**
