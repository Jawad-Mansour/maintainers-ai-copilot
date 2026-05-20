# Phase 5-T — Non-UI Backend Tests

**Status:** ✅ All passed (2026-05-20)
**Scope:** 3 new test files, 19 new tests | Total suite: 106/106

| File | Tests | What it covers |
|------|-------|----------------|
| `tests/test_phase5_tools.py` | 11 | Tool schemas, executor dispatch, tool-calling loop |
| `tests/test_phase5_widgets.py` | 8 | Widget CRUD routes, admin enforcement, /widget.js |
| `tests/test_phase5_stream.py` | 5 | SSE content-type, auth guard, CORS origin enforcement |

---

## test_phase5_tools.py — 11 tests

### Tool Definition Tests (6)

**`test_all_tools_have_required_fields`** — Verifies all 5 entries in `ALL_TOOLS` have
`type == "function"`, `function.name`, `function.description`, `function.parameters`,
and `parameters.type == "object"`. Catches schema typos before they reach the OpenAI API.

**`test_tool_names_are_unique`** — Asserts no two tools share the same name. Duplicate
names would cause OpenAI to silently ignore one of them.

**`test_expected_tool_names_present`** — Set equality check: exactly the 5 required
tool names are present, no more, no less.

**`test_classify_issue_requires_text`** — `text` must be in both `properties` and
`required`. OpenAI will error if a required parameter is missing in the schema.

**`test_search_knowledge_base_requires_query`** — Same check for `query`.

**`test_write_memory_requires_summary`** — Same check for `summary`.

### Tool Executor Tests (3)

**`test_execute_classify_issue`** — Creates a `ToolContext` with a mocked
`modelserver_client.classify()` returning `["bug"]`. Calls `execute_tool("classify_issue", ...)`
and asserts the return is `"bug"`.

**`test_execute_classify_returns_unknown_on_empty`** — When `classify()` returns `[]`,
`execute_tool` must return `"unknown"` (not crash or return empty string).

**`test_execute_extract_entities`** — Mocks `modelserver_client.ner()` returning a
list of entity dicts. Verifies `execute_tool` returns valid JSON with the entities.

**`test_execute_write_memory`** — Patches `app.services.memory_service.save_memory`
(the import path the executor uses at runtime). Verifies the tool returns a "Memory saved"
confirmation string.

**`test_execute_unknown_tool_raises`** — Calls `execute_tool("does_not_exist", ...)`.
Must raise `ToolFailure` matching "Unknown tool".

### Tool-Calling Loop Tests (2)

**`test_chat_tool_loop_no_tools_called`** — Mocks the LLM to return a response with
`tool_calls=None` on the first call (LLM goes straight to final answer). Verifies:
- `result.reply == "Here is the answer."`
- `result.label == "unknown"` (no classify tool was called)
- No exception raised

**`test_chat_tool_loop_classify_sets_label`** — Mocks the LLM with two calls:
1. First call: returns `tool_calls=[classify_issue(text="crash on merge")]`
2. Second call: returns final answer `"This is a bug."` with `tool_calls=None`

Mocks `modelserver_client.classify()` to return `["bug"]`.
Verifies `result.label == "bug"` — the label was captured from the tool result.

---

## test_phase5_widgets.py — 8 tests

All tests use an `admin_client` fixture (role="admin") or `user_client` (role="user").
All widget service calls are mocked — no DB needed.

**`test_create_widget_returns_201`** — POST /widgets with valid payload → 201. Response
body contains `name` and `id`.

**`test_list_widgets_returns_200`** — GET /widgets → 200. Response is a list.

**`test_get_widget_returns_200`** — GET /widgets/{id} → 200. Response contains the
widget `id`.

**`test_update_widget_returns_200`** — PUT /widgets/{id} with partial update → 200.

**`test_delete_widget_returns_204`** — DELETE /widgets/{id} → 204 (no body).

**`test_create_widget_forbidden_for_non_admin`** — Uses `user_client` (role="user").
POST /widgets → 403, `error == "permission_denied"`. Enforces admin-only requirement.

**`test_get_widget_not_found`** — `widget_service.get_widget` raises `NotFoundError`.
GET /widgets/{unknown_id} → 404, `error == "not_found"`.

**`test_widget_js_returns_javascript`** — GET /widgets/widget.js?widget_id=xxx → 200
with `content-type: application/javascript`. Response body contains the widget_id and
"iframe". No auth required (public endpoint).

---

## test_phase5_stream.py — 5 tests

**`test_stream_returns_event_stream_content_type`** — Mocks `chat_service.stream_chat`
to return a pre-built SSE generator. POST /chat/stream → 200,
`content-type: text/event-stream`. The SSE content-type is the contract for browser
`EventSource` clients.

**`test_stream_response_contains_token_events`** — Verifies the response body contains
the strings "token" and "done" — the two event types the React widget parses.

**`test_stream_requires_auth`** — `get_current_user` raises `AuthenticationError`.
POST /chat/stream → 401. Streaming endpoint must not be publicly accessible.

**`test_stream_cors_blocked_by_widget_allowed_origins`** — Creates a mock widget with
`allowed_origins=["http://allowed.com"]`. Sends request with
`Origin: http://evil.com` and `widget_id`. Expects 403, `error == "permission_denied"`.
This is the CORS enforcement test.

**`test_stream_allowed_origin_passes`** — Same setup but with
`Origin: http://allowed.com`. Expects 200. Confirms the allowlist works bidirectionally:
blocks bad origins, passes good ones.

---

## Phase 4 Test Updates

3 changes were needed to keep the 87 Phase 4 tests green after the chat_service rewrite:

| Change | Why |
|--------|-----|
| `choice.message.tool_calls = None` in mock | New LLM call includes `tools=` param; mock must signal no tools called |
| `load_prompt` mock returns `"{memories}"` not `"{label} {memories} {chunks}"` | system.md now uses `{memories}` only |
| `result.label == "unknown"` not `"bug"` | Label only set when LLM calls classify_issue tool; mock LLM calls no tools |

These changes are accurate reflections of the new design — not workarounds.

---

## Pass Criteria — All Met ✅

- [x] `pytest tests/test_phase5_tools.py -v` → **11 passed**
  - [x] `test_all_tools_have_required_fields`
  - [x] `test_tool_names_are_unique`
  - [x] `test_expected_tool_names_present`
  - [x] `test_classify_issue_requires_text`
  - [x] `test_search_knowledge_base_requires_query`
  - [x] `test_write_memory_requires_summary`
  - [x] `test_execute_classify_issue`
  - [x] `test_execute_classify_returns_unknown_on_empty`
  - [x] `test_execute_extract_entities`
  - [x] `test_execute_write_memory`
  - [x] `test_execute_unknown_tool_raises`
  - [x] `test_chat_tool_loop_no_tools_called`
  - [x] `test_chat_tool_loop_classify_sets_label`

- [x] `pytest tests/test_phase5_widgets.py -v` → **8 passed**
  - [x] `test_create_widget_returns_201`
  - [x] `test_list_widgets_returns_200`
  - [x] `test_get_widget_returns_200`
  - [x] `test_update_widget_returns_200`
  - [x] `test_delete_widget_returns_204`
  - [x] `test_create_widget_forbidden_for_non_admin`
  - [x] `test_get_widget_not_found`
  - [x] `test_widget_js_returns_javascript`

- [x] `pytest tests/test_phase5_stream.py -v` → **5 passed**
  - [x] `test_stream_returns_event_stream_content_type`
  - [x] `test_stream_response_contains_token_events`
  - [x] `test_stream_requires_auth`
  - [x] `test_stream_cors_blocked_by_widget_allowed_origins`
  - [x] `test_stream_allowed_origin_passes`

- [x] All Phase 1–4 tests still passing (87/87)
- [x] Total suite: **106/106 passed**

**Phase 5 Non-UI passed. Ready for 5-C Streamlit → 5-D React → 5-E Host.**
