# Phase 5 — Tool-Calling Agent + Widget Backend (Non-UI)

**Status:** ✅ Non-UI complete (2026-05-20) | ⏳ UI pending (5-C Streamlit, 5-D React, 5-E host)
**Tests:** 106/106 passing

---

## Goal

Replace the hardcoded classify → RAG → LLM pipeline with a proper tool-calling agent loop
where the LLM decides which tools to invoke. Add the widget infrastructure (CRUD, loader
script, SSE streaming) so the chat backend can be embedded in external websites.

---

## What Changed vs Phase 4

| Phase 4 (old) | Phase 5 (new) |
|---------------|---------------|
| Hardcoded: classify → RAG → LLM | LLM-driven: tool-calling loop (LLM decides) |
| One POST /chat endpoint | POST /chat + POST /chat/stream (SSE) |
| No widget concept | Widget CRUD (admin), /widget.js loader |
| No CORS | CORSMiddleware + per-widget origin enforcement |
| 87 tests | 106 tests (+19) |

---

## Sub-phases

### 5-A: Tool-Calling LLM Refactor

**Files:** `api/app/tools/`, `api/app/services/chat_service.py`

The chat pipeline is no longer hardcoded. The LLM receives 5 tool schemas and decides
which tools to invoke for each user message.

**5 Tools defined in `api/app/tools/definitions.py`:**

```
┌─────────────────────────────────────────────────────────────────┐
│  Tool Name            │  What it does                           │
├─────────────────────────────────────────────────────────────────┤
│  classify_issue       │  POST modelserver /classify → label     │
│  search_knowledge_base│  Hybrid RAG search → top-k chunks       │
│  extract_entities     │  POST modelserver /ner → entities       │
│  summarize_thread     │  LLM summarizes current conversation    │
│  write_memory         │  embed + persist to memories table      │
└─────────────────────────────────────────────────────────────────┘
```

Each tool schema is a standard OpenAI function object:
```python
{
    "type": "function",
    "function": {
        "name": "classify_issue",
        "description": "...",
        "parameters": {"type": "object", "properties": {...}, "required": [...]}
    }
}
```

**`ToolContext` dataclass (`api/app/tools/executor.py`):**

Groups all dependencies a tool may need:
```python
@dataclass
class ToolContext:
    db: AsyncSession
    user_id: UUID
    conversation_id: UUID
    api_key: str
    minio_client: Minio
    modelserver_client: ModelServerClient
    history: list[dict]        # for summarize_thread
```

**Tool-calling loop in `chat_service._run_tool_loop()`:**

```
messages = [system, ...history, user_message]

for iteration in range(5):   ← max 5 tool rounds
    resp = LLM(messages, tools=ALL_TOOLS)
    msg = resp.choices[0].message

    if no msg.tool_calls:
        return msg.content   ← FINAL ANSWER

    for each tool_call in msg.tool_calls:
        result = execute_tool(name, args, ctx)
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

# fallback: force final response without tools
```

**Key tracking during the loop:**
- If `classify_issue` tool called → `label = result` (sets ChatResponse.label)
- If `search_knowledge_base` called → `sources.extend(...)` (sets ChatResponse.sources)
- `write_memory` calls `memory_service.save_memory()` — audit log entry included

**Why max 5 iterations?**
Prevents the LLM from getting stuck in recursive tool loops. Real-world agents rarely need
more than 2-3 tool calls per turn for issue triage.

**What memories are still pre-loaded (not a tool)?**
Long-term memories are always injected into the system prompt as background context.
The LLM doesn't need to call a "get_memories" tool — relevant memories are already there.

---

### 5-B: Widget Backend

**Files:** `api/app/repositories/widget_repo.py`, `api/app/services/widget_service.py`,
`api/app/api/routes/widgets.py`, `api/app/api/routes/chat.py` (stream endpoint),
`api/main.py` (CORS + router)

#### Widget Model (from Phase 1 migration)

```python
class Widget(Base):
    __tablename__ = "widgets"
    id: UUID
    owner_id: UUID      # FK → users (admin who created it)
    name: str
    allowed_origins: list[str]   # CORS allowlist for this widget
    theme: dict | None           # JSON: {"color": "#1a1a1a", ...}
    greeting: str                # "How can I help?"
    enabled_tools: list[str]     # tools this widget exposes
    is_active: bool
```

#### Widget CRUD Routes (all admin-only)

```
POST   /widgets           → 201 WidgetOut   (create)
GET    /widgets            → list[WidgetOut] (list all)
GET    /widgets/{id}       → WidgetOut       (get one)
PUT    /widgets/{id}       → WidgetOut       (update)
DELETE /widgets/{id}       → 204             (delete)
GET    /widgets/widget.js?widget_id=xxx → JavaScript loader script
```

#### GET /widget.js — Loader Script

The embed snippet a website includes:
```html
<script src="https://api.example.com/widgets/widget.js?widget_id=abc123"></script>
```

This returns JavaScript that creates an `<iframe>` pointing to the chat widget UI:
```javascript
(function() {
  var widgetId = "abc123";
  var iframe = document.createElement("iframe");
  iframe.src = "/chat-widget?widget_id=" + widgetId;
  iframe.style.cssText = "position:fixed;bottom:20px;right:20px;...";
  document.body.appendChild(iframe);
})();
```

#### POST /chat/stream — SSE Streaming

```
POST /chat/stream?widget_id=<optional>
Authorization: Bearer <jwt>
Content-Type: application/json

{"message": "...", "conversation_id": "..."}

→ 200 text/event-stream
data: {"type": "token", "content": "Use"}
data: {"type": "token", "content": " .loc"}
data: {"type": "done", "label": "bug", "sources": ["pandas/issues"]}
```

**Stream flow:**
1. Tool-calling loop runs first (non-streaming) to resolve all tool calls
2. Final LLM response is streamed token-by-token via `AsyncOpenAI.chat.completions.stream()`
3. Each token → `data: {"type": "token", "content": "..."}` SSE event
4. Final event → `data: {"type": "done", "label": "...", "sources": [...]}`
5. Messages and Redis history persisted after streaming completes

**Why resolve tools before streaming?**
Tool calls require full LLM responses (to know if tool_calls is present). We can only
stream the generation phase — once we know the LLM is giving a final answer, not calling
another tool.

#### Dynamic CORS + CSP

- Global `CORSMiddleware(allow_origins=["*"])` added to `main.py` for general API access
- Per-widget origin enforcement in `POST /chat/stream`:
  - If `widget_id` provided and `Origin` header not in `widget.allowed_origins` → 403
  - Prevents other websites from using your widget's API key

```python
if widget_id is not None:
    widget = await widget_repo.get(db, widget_id)
    if widget and widget.allowed_origins:
        origin = request.headers.get("origin", "")
        if origin and origin not in widget.allowed_origins:
            raise PermissionDenied(f"Origin '{origin}' not allowed for this widget")
```

---

## Files Created / Modified

### New files

| File | Purpose |
|------|---------|
| `api/app/tools/definitions.py` | 5 OpenAI tool schemas (ALL_TOOLS list) |
| `api/app/tools/executor.py` | ToolContext dataclass + execute_tool() dispatch |
| `api/app/repositories/widget_repo.py` | Widget ORM CRUD |
| `api/app/services/widget_service.py` | Widget business logic |
| `api/app/api/routes/widgets.py` | CRUD + /widget.js loader |

### Modified files

| File | Change |
|------|--------|
| `api/app/services/chat_service.py` | Full rewrite: tool-calling loop + stream_chat() |
| `api/app/api/routes/chat.py` | Added POST /chat/stream SSE endpoint |
| `api/app/domain/models.py` | Added WidgetCreate, WidgetUpdate, WidgetOut |
| `api/main.py` | CORSMiddleware + widgets_router |
| `api/prompts/system.md` | Simplified for tool-calling (removed hardcoded {label}/{chunks}) |
| `api/prompts/summarize.md` | Updated format: {focus} + {conversation} |

---

## Prompt Changes

### system.md (was)
```
You are a GitHub maintainer copilot helping with {label} issues.
Relevant memories from past conversations: {memories}
Relevant knowledge base: {chunks}
Answer concisely and accurately. If unsure, say so.
```

### system.md (now)
```
You are a GitHub maintainer copilot. Help triage, analyze, and respond to issues accurately.
Long-term memories about this user: {memories}
You have tools available: classify_issue, search_knowledge_base, extract_entities,
summarize_thread, write_memory.
Use them when they improve your response. Answer concisely. If unsure, say so.
```

**Why the change?** The old prompt referenced `{label}` and `{chunks}` which were set
by the hardcoded pipeline. Now the LLM calls tools itself — so the prompt describes the
tools rather than injecting the results.

---

## Data Flow — Tool-Calling Chat Request

```
POST /chat
    │
    ▼
chat_service.chat(db, req, user_id, api_key, minio, modelserver, langfuse, redis)
    │
    ├── [1] conversation_repo.get() → ownership check (PermissionDenied if wrong user)
    │
    ├── [2] memory_service.get_relevant_memories()
    │       → memories_text for system prompt (pre-loaded, not a tool)
    │
    ├── [3] _get_history(redis, db) → last 10 conversation turns
    │
    ├── [4] Build messages:
    │       [system(memories), ...history[-10:], user_message]
    │
    ├── [5] Build ToolContext (groups all deps for tool executor)
    │
    └── [6] _run_tool_loop(messages, ctx):
            │
            ├── LLM call #1 (with ALL_TOOLS)
            │       ↓ no tool_calls? → return reply immediately
            │       ↓ tool_calls present:
            │
            ├── execute_tool("classify_issue", ...) → "bug" → label = "bug"
            │       └── modelserver_client.classify()
            │
            ├── execute_tool("search_knowledge_base", ...) → JSON chunks
            │       └── rag_service.search() (full Phase 3 pipeline)
            │
            ├── LLM call #2 (with tool results in messages)
            │       ↓ no tool_calls? → return final reply
            │
            ├── persist: message_repo.create() × 2 + redis.set()
            └── → ChatResponse(reply, label, sources)
```

---

## Pending (UI phases)

| Sub | What | Status |
|-----|------|--------|
| 5-C | Streamlit — login, SSE chat, memory inspector, widget config | ⏳ |
| 5-D | React widget — Vite + Tailwind, bubble, SSE, theme | ⏳ (you run npm build) |
| 5-E | Host demo page — index.html + nginx embed demo | ⏳ |
