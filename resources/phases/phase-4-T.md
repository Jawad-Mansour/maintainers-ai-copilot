# Phase 4-T — Chatbot Pipeline + Memory Tests

**Status:** ✅ All passed (2026-05-20)
**Scope:** 3 test files, 16 tests — chat service, memory service, and `/chat` route.

---

## Goal

Verify that the chat pipeline enforces conversation ownership, never auto-saves memories,
correctly serves conversation history from Redis before hitting the database, updates the
Redis cache after every reply, returns the correct response shape, and routes errors back
to the caller cleanly. Memory service tests verify that every long-term write produces an
audit log row and that `save_memory` does not commit its own transaction. All 16 tests
run without a real database, Vault, or OpenAI API key.

---

## Why These Tests Matter More Than Usual

Phase 4 introduces several subtle behavioral contracts that are easy to violate
silently:

- **D14 violation (no auto-save):** The original Phase 4 implementation had
  `save_memory()` called automatically on every chat turn. This was caught and removed,
  but without a test, a future refactor could re-introduce it silently.
- **Ownership check:** If `conversation_repo.get()` is not called before the LLM is
  invoked, one user can trigger OpenAI API calls billed to the operator on behalf of
  another user's conversation.
- **Redis-first history:** If the Redis cache hit is not checked before querying the
  database, every chat turn hits the DB even when the cache is warm. At scale this is
  a significant unnecessary load.
- **Audit log on memory write:** The assignment requires every long-term memory write
  to produce an audit row. If the `audit_repo.log()` call is ever removed, the compliance
  requirement is silently broken.

Each of these is a behavioral contract, not a type error. Static analysis and type
checkers cannot catch them. Tests can.

---

## Bug Fixes Applied Before Phase 4-T

Eight bugs were identified and fixed before writing these tests. The tests lock in
the correct behavior so the bugs cannot return:

| Bug | Description | Fix |
|-----|-------------|-----|
| D14 violation | `save_memory()` was called on every chat turn automatically | Removed the auto-call; memory is only written when the user explicitly triggers the `write_memory` tool (Phase 5) |
| Missing ownership check | The service called the LLM before verifying the conversation belonged to the requesting user | Added `conversation_repo.get()` + `PermissionDenied` check at the start of `chat()` |
| Reranker not wired | `rag_service.search()` fetched 20 candidates but never called the reranker | Wired `modelserver_client.rerank()` and sliced to `top_k` after reranking |
| Missing prompts directory | `api/prompts/` did not exist; `load_prompt()` would crash on first call | Created `api/prompts/system.md`, `hyde.md`, `summarize.md` with appropriate template content |
| Redis not wired | Chat service called `message_repo.list_by_conversation()` every time instead of checking Redis | Wired `_get_history()` using `redis.get()` / `redis.set()` with 24h TTL |
| Missing modelserver health check | API could boot without the modelserver being up | Added `httpx.AsyncClient.get(modelserver_host/health)` to the lifespan boot guard |
| Missing audit log on memory write | `save_memory()` did not call `audit_repo.log()` | Added `audit_repo.log(db, action="write_memory", actor_id=user_id, target_id=memory.id)` |
| No ToolFailure recovery | `classify()` and `rag_search()` exceptions propagated to the caller | Wrapped both in `try/except ToolFailure` with fallback to `label="unknown"` / `chunks=[]` |

---

## Test Files

### `tests/test_phase4_chat_service.py` — 6 tests

All tests in this file call the real `chat()` function from `chat_service.py` with all
external dependencies mocked: `conversation_repo`, `memory_service`, `rag_search`,
`message_repo`, `AsyncOpenAI`, and `load_prompt`. The Redis client is an `AsyncMock`
with `get()` returning `None` by default (cache miss) or a cached list (cache hit).

**`test_chat_raises_not_found_for_missing_conversation`** — Mocks
`conversation_repo.get` to return `None`. Calls `chat()`. Asserts `NotFoundError` is
raised with the message `"Conversation not found"`. This is the guard against requesting
an LLM call for a conversation that no longer exists (e.g., it was deleted between the
UI load and the message send).

**`test_chat_raises_permission_denied_for_wrong_user`** — Mocks `conversation_repo.get`
to return a conversation owned by `OTHER_ID`. Calls `chat()` with `user_id=OWNER_ID`.
Asserts `PermissionDenied` with `"Not your conversation"`. This is the critical
multi-tenancy test: without this check, user A could inject messages into user B's
conversation and consume B's LLM budget.

**`test_chat_does_not_auto_save_memory`** — Patches `memory_service.save_memory` and
monitors whether it is called after a successful chat turn. Asserts
`mock_save.assert_not_called()`. This directly verifies the D14 design decision: memories
are only written explicitly (via the `write_memory` tool in Phase 5), never automatically
on every turn. If someone re-introduces the auto-save, this test fails immediately.

**`test_chat_serves_history_from_redis_cache`** — Sets `redis.get.return_value` to a
JSON-encoded list of prior messages (simulating a cache hit). Asserts that
`message_repo.list_by_conversation` is NOT called. This verifies the Redis-first
contract: when the cache is warm, the database is not consulted for history. Without
this optimization, every chat message would trigger a database query for history even
when it's already available in Redis.

**`test_chat_updates_redis_after_reply`** — Calls `chat()` with an empty cache (cache
miss). After the call completes, asserts that `redis.set` was called once. Extracts
the stored value, parses it, and asserts that the last two entries are the user message
and assistant reply in the correct order. This verifies that the cache is populated
after every reply so the next message can be served from cache.

**`test_chat_returns_correct_response_shape`** — Passes a mock `ChunkResult` with a
known `source` field through the full pipeline. Asserts that the returned `ChatResponse`
has:
- `reply` equal to the mocked OpenAI response text
- `label` equal to the mocked classifier output (`"bug"`)
- `sources` containing the chunk's `source` value

This is the end-to-end shape test: if the service computes the right answer but
assembles the response object incorrectly, the client receives wrong data. The `sources`
field in particular is a set-deduplicated list built from chunk sources — this test
confirms the deduplication and list conversion are correct.

---

### `tests/test_phase4_memory.py` — 5 tests

This file tests `memory_service.get_relevant_memories()` and `memory_service.save_memory()`
with all repository calls mocked.

**`test_get_relevant_memories_returns_summaries`** — Mocks `embed_one` (returns a
fake 1536-dim vector) and `memory_repo.search_by_similarity` (returns two rows with
`summary` fields). Asserts the returned list equals `["User prefers concise answers",
"Bug in auth module"]`. Verifies that the service correctly extracts the `summary` field
from the raw rows and returns a flat list of strings — not a list of dicts or ORM objects.

**`test_get_relevant_memories_returns_empty_list_when_none`** — Mocks
`search_by_similarity` to return an empty list. Asserts the service returns `[]`.
Confirms correct handling of the "no relevant memories" case (as opposed to returning
`None` or raising an exception).

**`test_save_memory_embeds_and_calls_create`** — Calls `save_memory()` and asserts
both `embed_one` and `memory_repo.create` were called with the correct arguments.
`embed_one` should be called with the memory summary text and the API key.
`memory_repo.create` should be called with `(db, user_id, summary_text, embedding_vector)`.
This confirms the embedding and write pipeline is correctly wired.

**`test_save_memory_writes_audit_log`** — Calls `save_memory()` and asserts
`audit_repo.log` was called once. Inspects the call's keyword arguments and asserts:
- `action == "write_memory"`
- `actor_id == USER_ID`
- `target_id == mock_memory.id`

This directly verifies the compliance requirement: every long-term memory write must
produce an immutable audit trail. The `target_id` being the memory row's own ID means
the audit log entry points to the specific memory that was created.

**`test_save_memory_does_not_commit`** — Calls `save_memory()` with a mocked `mock_db`.
After the call, asserts `mock_db.commit.assert_not_called()`. This verifies the
transaction ownership contract: `save_memory()` adds rows to the session but does NOT
commit. The caller (the chat service, or the future `write_memory` tool handler) owns
the transaction boundary. If `save_memory` committed independently, a caller that
encountered an error after `save_memory()` but before its own commit would have a memory
written without the associated chat message — a consistency violation.

---

### `tests/test_phase4_routes.py` — 5 tests

HTTP-level tests for `POST /chat`. Uses a `client` fixture that injects all mock
dependencies and sets `app.state.langfuse = mock_langfuse` (required by the chat route,
which passes `app.state.langfuse` to the service).

**`test_chat_returns_200`** — POSTs to `/chat` with a valid body. Mocks
`chat_service.chat` to return a `ChatResponse`. Asserts HTTP 200 and that
`body["reply"]`, `body["label"]`, and `body["sources"]` match the mocked values.

**`test_chat_response_has_correct_schema`** — Asserts that the response body contains
at least the keys `reply`, `label`, and `sources`, and that `sources` is a list. This
is a schema contract test — the Streamlit UI and React widget in Phase 5 depend on this
exact response structure.

**`test_chat_requires_auth`** — Overrides `get_current_user` to raise
`AuthenticationError`. Asserts HTTP 401. Confirms the chat endpoint is not public.
Anonymous users must not be able to chat.

**`test_chat_propagates_not_found`** — Mocks `chat_service.chat` to raise
`NotFoundError("Conversation not found")`. Asserts HTTP 404 and
`response.json()["error"] == "not_found"`. Verifies the FastAPI exception handler
correctly maps the domain error to the right HTTP status and machine-readable code.

**`test_chat_propagates_permission_denied`** — Mocks `chat_service.chat` to raise
`PermissionDenied("Not your conversation")`. Asserts HTTP 403 and
`response.json()["error"] == "permission_denied"`. Together with the previous test,
these confirm that the single `AppError` handler in `main.py` correctly dispatches
all domain errors — not just the ones the developer was thinking about when writing
the handler.

---

## Ruff Fixes Applied

Before tests could run, ruff found several issues that required manual fixes:

**`F821` — Undefined name `api`** in `test_phase4_chat_service.py` line 28:
```python
def _make_chat_req() -> api.app.domain.models.ChatRequest:  # type: ignore[name-defined]
    from api.app.domain.models import ChatRequest
    return ChatRequest(...)
```
The ruff auto-fix for `# type: ignore` had removed the quotes around the annotation,
making `api` a runtime name lookup — but `api` is not imported at module level, so
ruff flagged it as `F821` (undefined name). The fix was to add a top-level import:
```python
from app.domain.models import ChatRequest
```
And simplify the function to use it directly:
```python
def _make_chat_req() -> ChatRequest:
    return ChatRequest(message="fix the null pointer", conversation_id=CONV_ID)
```

**`SIM117` — Nested `with` statements** (same pattern as Phase 2-T, 2 occurrences):
```python
with patch("...conversation_repo.get", return_value=None):
    with pytest.raises(NotFoundError, match="Conversation not found"):
```
Fixed by collapsing into a single parenthesized `with`.

**`E501` — Long lines** in mock patch calls (8 occurrences). All were `patch()` calls
with long module paths. Fixed by splitting the string and keyword argument across lines:
```python
patch(
    "api.app.services.chat_service.memory_service.get_relevant_memories",
    return_value=[],
),
```

---

## Pass Criteria — All Met ✅

- [x] `pytest tests/test_phase4_chat_service.py -v` → **6 passed**
- [x] `pytest tests/test_phase4_memory.py -v` → **5 passed**
- [x] `pytest tests/test_phase4_routes.py -v` → **5 passed**
- [x] **Total: 16/16 passed**

**Combined across all phases: 87/87 tests pass.**

---

## Full Suite Summary

| Phase | File(s) | Tests | Status |
|-------|---------|-------|--------|
| 1 | test_phase1_boot_guard, test_phase1_db, test_phase1_redis | 12 | ✅ |
| 2 | test_phase2_auth, crud, crud_routes, exceptions, redaction | 39 | ✅ |
| 3 | test_phase3_chunker, rag_service, routes | 20 | ✅ |
| 4 | test_phase4_chat_service, memory, routes | 16 | ✅ |
| **Total** | | **87** | **✅** |

All 87 tests pass in ~14 seconds on the local machine. Zero Docker, Vault, or API keys
required. Pre-commit hooks (ruff, mypy, gitleaks) all pass clean.

**Phase 4-T passed. Cleared to proceed to Phase 5.**
