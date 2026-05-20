# Phase 2 — API Core

**Status:** ✅ Complete (2026-05-20)
**Commit:** `feat: phase 2 — auth, CRUD, exceptions, redaction, observability`

---

## Goal

The API has a working authentication system (register, login, JWT), full conversation
and message CRUD, a domain exception hierarchy mapped to clean HTTP responses, a
redaction layer that strips secrets from all log output, and structured JSON logging
with trace ID injection. DB and Redis dependencies are fully wired.

---

## Why This Phase Exists

Phase 1 gave us infrastructure with a stub API that could only answer `GET /health`.
Phase 2 makes the API usable:
- Phase 3 RAG pipeline needs authenticated users to ingest and search chunks
- Phase 4 agent needs conversations and messages to store chat history
- Phase 5 widget config needs the admin role check from this phase
- Phase 6 CI needs the redaction test that proves no secrets leak through logs

Everything built here is tested in Phase 2-T using mock-based unit tests — no Docker
required. The full stack integration tests run after all code phases are complete.

---

## Files Created / Modified

### `api/requirements.txt`

**Change:** Removed `fastapi-users[sqlalchemy]>=13.0.0`. Added:
```
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
```

**Why replace fastapi-users with custom auth?**
`fastapi-users` requires inheriting from its own base models, schemas, and user
manager — all of which conflict with our existing SQLAlchemy `User` ORM model and
Pydantic domain model structure. Implementing JWT auth directly with `python-jose`
(JWT encode/decode) and `passlib` (bcrypt hashing) gives us full control with far
less boilerplate. The feature set is identical: email + password registration, JWT
tokens, role checks, Vault-sourced signing key.

**Why `python-jose[cryptography]` over `PyJWT`?**
`python-jose` has built-in support for JWK, JWKS endpoints, and multiple algorithms
out of the box. The `[cryptography]` extra enables RS256 if we ever rotate to
asymmetric keys. `PyJWT` would work equally well for our HS256 use case.

**Why `passlib[bcrypt]`?**
`passlib` provides a stable, algorithm-agnostic `CryptContext` API. Bcrypt is the
industry standard for password hashing — slow by design (prevents brute-force),
salted automatically, work factor is configurable. The `deprecated="auto"` policy
means if we add a stronger scheme later, existing bcrypt hashes are automatically
re-hashed on next login.

---

### `api/app/exceptions.py`

Domain exception hierarchy, distinct from infrastructure exceptions (SQLAlchemy
`IntegrityError`, `redis.ConnectionError`, etc.).

```python
class AppError(Exception):
    message: str
    code: str        # machine-readable string, not HTTP status
    status_code: int # HTTP status code

class NotFoundError(AppError)      # 404 — resource doesn't exist
class PermissionDenied(AppError)   # 403 — authenticated but not authorized
class AuthenticationError(AppError)# 401 — missing/invalid/expired credentials
class ConflictError(AppError)      # 409 — duplicate (email already registered)
class ToolFailure(AppError)        # 502 — external tool call failed (Phase 4)
class ValidationError(AppError)    # 422 — business-rule violation
class RateLimitError(AppError)     # 429 — rate limit hit (Phase 4 LLM calls)
```

**Why a custom hierarchy instead of `HTTPException` directly in services?**
Services must not know about HTTP. If a service raises `HTTPException(status_code=404)`,
the service layer is coupled to the HTTP transport. If we ever add a CLI or a message
queue consumer, the 404 makes no sense. `NotFoundError` is transport-agnostic. The
single exception handler in `main.py` translates it to HTTP 404.

**Why `code` as a string instead of just using `status_code`?**
Two errors can share the same HTTP status but mean different things to a client.
Example: `authentication_error` vs `token_expired` are both 401 but require different
UX handling. The `code` field lets the frontend switch on a stable string key rather
than parsing the human-readable `message`.

**Why `ToolFailure` at 502 (Bad Gateway)?**
When the classifier or NER endpoint is down, the API is acting as a gateway to the
modelserver. A 502 is semantically correct: our service is healthy, but an upstream
it depends on is not responding.

---

### `api/app/infra/redaction.py`

Strips secrets before any log line, trace span, or memory write leaves the service.

**Patterns (justified in full in `SECURITY.md`):**

| Pattern | Redacts | Why |
|---------|---------|-----|
| `sk-[a-zA-Z0-9]{20,}` | OpenAI API keys | OpenAI key format; 48+ chars after `sk-` |
| `ghp_[a-zA-Z0-9]{36}` | GitHub PAT (classic) | GitHub classic token format |
| `ghs_[a-zA-Z0-9]{36}` | GitHub server-to-server token | Same format, different prefix |
| `github_pat_[a-zA-Z0-9_]{82}` | GitHub fine-grained PAT | New format since 2022 |
| `(?i)password\s*[=:]\s*\S+` | Passwords in key=value form | `password=secret` in stack traces |
| `Bearer [A-Za-z0-9...]+` | JWT tokens in Authorization headers | Users paste curl commands into issues |
| `(?i)secret[_-]?key\s*[=:]\s*\S{20,}` | MinIO/AWS secret keys | `secret_key=...` in config dumps |

**Why is redaction a separate module in `app/infra/` rather than inline in the logger?**
The assignment requires redaction before log lines, trace spans, AND memory writes.
If redaction lived only in the logger, a trace span or a memory write could still leak
secrets. A standalone `redact(text)` function is called explicitly before each of the
three destinations.

**Why regex and not a blocklist of known values?**
A blocklist requires knowing the secret value in advance. Regex catches any key
matching the pattern, including keys that were rotated after the app started.

**The redaction test (`test_phase2_redaction.py`) asserts:**
A message containing `sk-abc123...` (fake OpenAI key) is never present in the
processed output — proving the pattern fires correctly.

---

### `api/app/infra/observability.py`

Structlog configuration with a custom redacting processor.

**Key design decisions:**

**Why structlog over Python's stdlib `logging`?**
Structlog produces structured JSON output where every field is a key-value pair.
`logging.info(f"user={user_id} action=login")` produces an unstructured string.
Structured logs are machine-parseable by log aggregators (Datadog, Loki, CloudWatch).

**Why a custom `_redacting_processor`?**
Structlog's processor chain runs before the log line is written. By placing the
redacting processor in the chain, every log line from every module is automatically
redacted — no opt-in required. `event` and all string values in the dict are redacted.

**`bind_trace_id(trace_id)` / `clear_trace_id()`:**
Uses `structlog.contextvars` to bind a trace ID to the current async context. Every
log line emitted during a request automatically carries `trace_id` — making logs
and Langfuse traces joinable by that field.

**`configure_logging()` is called in `main.py` lifespan:**
Not at import time. Calling it at import time would configure the logger before the
app has a chance to set up its context (e.g., in tests, `configure_logging()` would
run before pytest captures output).

---

### `api/app/infra/redis_client.py`

```python
CONVERSATION_DB = 0   # 24h TTL — short-term conversation memory
CACHE_DB = 1          # 5min TTL — GET /me, GET /conversations

def build_redis(host, port=6379, db=CONVERSATION_DB) -> aioredis.Redis
def build_cache_redis(host, port=6379) -> aioredis.Redis
```

**Why `decode_responses=True`?**
Redis returns bytes by default. `decode_responses=True` auto-decodes to Python
strings, eliminating manual `b"key".decode("utf-8")` calls everywhere.

**Why two separate factory functions?**
The conversation cache and API cache have different TTLs and different eviction
policies. Having separate factory functions makes it easy to pass different Redis
instances to different services without mixing TTL semantics.

---

### `api/app/infra/jwt_handler.py`

```python
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

def create_access_token(user_id, role, signing_key) -> str
def decode_access_token(token, signing_key) -> dict
```

**Why HS256 (symmetric) over RS256 (asymmetric)?**
For a single-service deployment, symmetric signing is simpler and equally secure.
RS256 is needed when multiple services need to verify tokens independently (the
verifier only needs the public key). Here, only the API service verifies tokens.

**Why 24h expiry?**
Matches the Redis conversation TTL. A user's session and their conversation history
expire at the same time — no orphaned Redis keys from expired sessions.

**Why does `decode_access_token` raise `AuthenticationError` instead of returning `None`?**
The caller (get_current_user dependency) would have to check the return value and
then raise its own error. Raising at the decode site keeps the error handling in one
place and ensures `AuthenticationError` (→ 401) is always the response for bad tokens.

---

### `api/app/domain/models.py`

Pydantic domain models — **distinct from SQLAlchemy ORM models** in `app/infra/db/models.py`.

| Class | Purpose | Used by |
|-------|---------|---------|
| `UserOut` | Response model for user data | `GET /auth/me`, dependency injection |
| `ConversationOut` | Response model for a conversation | All conversation routes |
| `MessageOut` | Response model for a message | All message routes |
| `RegisterRequest` | Input schema for registration | `POST /auth/register` |
| `LoginRequest` | Input schema for login | `POST /auth/login` |
| `LoginResponse` | Returns `access_token` | Register + login responses |
| `MessageCreate` | Input schema for new message | `POST /conversations/{id}/messages` |

**Why `model_config = ConfigDict(from_attributes=True)` on output models?**
`from_attributes=True` allows `UserOut.model_validate(orm_user)` where `orm_user`
is a SQLAlchemy ORM instance. Without it, pydantic only accepts dicts. This is the
bridge between the repository layer (ORM objects) and the service/route layer
(Pydantic domain objects).

**Why are ORM models not used directly in routes?**
SQLAlchemy ORM objects carry session state, lazy-loading relationships, and internal
tracking state. Returning them from routes would either serialize incorrectly or
accidentally trigger N+1 queries. Pydantic domain models are plain data with no side
effects.

---

### `api/app/repositories/`

Four new repository files. **All follow the same contract:**
- Accept `AsyncSession` as first argument
- Perform SQL operations only — no HTTP calls, no cache logic, no domain errors
- Do **not** call `db.commit()` — the service layer owns transactions

**`user_repo.py`**
- `get_by_email(db, email)` — used in login and register to check for duplicates
- `get_by_id(db, user_id)` — used in `get_current_user` dependency
- `create(db, email, hashed_password, role="user")` — does NOT commit; returns ORM User

**`conversation_repo.py`**
- `create(db, user_id)` — adds Conversation to session
- `get(db, conv_id)` — returns `Conversation | None`
- `list_by_user(db, user_id)` — ordered by `created_at DESC`
- `delete_by_id(db, conv_id)` — uses `DELETE` statement (not ORM delete for safety with FK cascades)

**`message_repo.py`**
- `create(db, conversation_id, role, content)` — role is `"user"` or `"assistant"`
- `list_by_conversation(db, conversation_id)` — ordered by `created_at ASC` (chronological)

**`audit_repo.py`**
- `log(db, actor_id, action, target_id, diff)` — append only, never updates or deletes
- Writes an `AuditLog` row for: conversation deletes, memory writes (Phase 4), widget config changes (Phase 5), role changes (Phase 5)
- Does NOT commit — service layer commits everything atomically

**Why no commits in repositories?**
If a service needs to write an audit log row AND delete a conversation in one atomic
operation, splitting commits across two repo calls would leave the database in an
intermediate state if the second operation fails. The service commits once after all
repo calls succeed, keeping the transaction boundary at the business-logic level.

---

### `api/app/services/auth_service.py`

```python
async def register(db, req: RegisterRequest, signing_key) -> LoginResponse
async def login(db, req: LoginRequest, signing_key) -> LoginResponse
async def get_me(db, user_id: str) -> UserOut
```

**Why does `register` return a `LoginResponse` (token) and not just `201 Created`?**
Registering and immediately having a working session is better UX. The alternative
(register → redirect to login → re-enter credentials) is unnecessary friction.

**Why is `signing_key` passed as an argument instead of imported from somewhere?**
The signing key comes from Vault (`app.state.secrets.jwt_signing_key`). Services
must not access `app.state` directly — they have no reference to the FastAPI `app`
object. The route passes the key as an argument. This also makes the service
independently testable without a running Vault.

**Transaction pattern in `register`:**
```python
user = await user_repo.create(db, email, hashed)  # adds to session, no commit
await db.commit()                                  # single commit
await db.refresh(user)                             # reload from DB to get server defaults
```
`db.refresh(user)` is needed after commit to populate server-side defaults like
`created_at` (set by `server_default=func.now()`). Without refresh, `user.created_at`
would be `None` on the returned object.

---

### `api/app/services/conversation_service.py`

```python
async def create_conversation(db, user_id) -> ConversationOut
async def list_conversations(db, user_id) -> list[ConversationOut]
async def delete_conversation(db, conv_id, user_id) -> None
async def add_message(db, conv_id, user_id, req: MessageCreate) -> MessageOut
async def list_messages(db, conv_id, user_id) -> list[MessageOut]
```

**Ownership check on every operation:**
Every operation that touches a specific conversation first calls
`conversation_repo.get(db, conv_id)`. If the conversation doesn't belong to the
requesting user, it raises `PermissionDenied`. This prevents a user from reading or
deleting another user's conversations even if they know the UUID.

**Atomic delete with audit log:**
```python
await audit_repo.log(db, actor_id=user_id, action="delete_conversation", target_id=conv_id)
await conversation_repo.delete_by_id(db, conv_id)
await db.commit()  # audit log + delete in one transaction
```
If the delete fails, the audit log entry is also rolled back (same session, same
transaction). The audit trail is never inconsistent.

---

### `api/app/api/routes/auth.py`

```
POST /auth/register   → 201 + LoginResponse
POST /auth/login      → 200 + LoginResponse
GET  /auth/me         → 200 + UserOut
```

**Routes are HTTP-only:** they call services, return responses, and inject
dependencies. No SQLAlchemy session management, no password hashing, no JWT logic.

**Why `GET /auth/me` instead of `GET /users/me`?**
Grouping all auth-related routes under `/auth` keeps the router focused. `/users`
will be an admin-only route in Phase 5 for listing and managing users.

---

### `api/app/api/routes/conversations.py`

```
POST   /conversations                          → 201 + ConversationOut
GET    /conversations                          → 200 + list[ConversationOut]
DELETE /conversations/{conv_id}                → 204
POST   /conversations/{conv_id}/messages       → 201 + MessageOut
GET    /conversations/{conv_id}/messages       → 200 + list[MessageOut]
```

All routes require a valid JWT (`Depends(get_current_user)`). No public endpoints.

**Why nested routes (`/conversations/{id}/messages`) instead of flat `/messages/{id}`?**
Nested routes express the parent-child relationship in the URL. A message only makes
sense in the context of a conversation. Flat `/messages` would require a
`?conversation_id=` query parameter, which is less RESTful and harder to validate.

---

### `api/app/api/routes/health.py` (updated)

```python
@router.get("/health")
async def health(request: Request) -> JSONResponse:
    checks = {}
    # Check DB: SELECT 1
    checks["db"] = "ok" | "error"
    # Check Redis: PING
    checks["redis"] = "ok" | "error"
    status = "ok" if all checks pass else "degraded"
    return JSONResponse({"status": status, "version": "0.1.0", "checks": checks}, status_code=200|503)
```

**Why does `/health` return 503 when degraded?**
Load balancers and `docker-compose --wait` use the HTTP status code to determine
health. A 200 with `"status": "degraded"` would be treated as healthy. A 503 causes
the load balancer to stop routing traffic and `docker-compose --wait` to report failure.

**Why check DB and Redis in /health but not Vault?**
Vault is checked at boot (refuse-to-boot). If the app is running, Vault was reachable
at startup. Vault's secrets are cached in `app.state` — the app can serve requests
even if Vault goes down after startup. DB and Redis, however, are checked on every
request; if they're down mid-run, the app is degraded.

---

### `api/dependencies.py` (updated)

**Real `get_db`:**
```python
async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        yield session
```
Each request gets its own session from the factory. `async with` auto-rolls back on
exception and closes the connection on exit.

**Real `get_redis`:**
```python
def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis_client
```
Redis client is shared across requests (connection pool under the hood).

**`get_current_user`:**
```python
async def get_current_user(credentials, secrets, db) -> UserOut:
    payload = decode_access_token(credentials.credentials, secrets.jwt_signing_key)
    user = await user_repo.get_by_id(db, UUID(payload["sub"]))
    if not user or not user.is_active:
        raise AuthenticationError(...)
    return UserOut.model_validate(user)
```
Validates the JWT signature, checks the user still exists and is active, and returns
a clean Pydantic `UserOut`. Routes receive `UserOut`, not the ORM `User` object.

**`require_admin`:**
```python
def require_admin(user: UserOut = Depends(get_current_user)) -> UserOut:
    if user.role != "admin":
        raise PermissionDenied("Admin access required")
    return user
```
Admin-only routes (`POST /widgets`, `GET /users`, etc.) use `Depends(require_admin)`
instead of `Depends(get_current_user)`. The admin check is in the dependency, not
scattered across route functions.

**Why does `get_current_user` hit the DB on every request?**
Token revocation. If an admin deactivates a user's account, the user's existing JWT
is still cryptographically valid until expiry. By checking `user.is_active` on every
request, revocation takes effect immediately. The 5-minute API cache in Redis DB 1
can be used to cache `get_me` responses in Phase 5 to reduce DB load.

---

### `api/main.py` (updated)

**New in lifespan:**
```python
app.state.session_factory = build_session_factory(secrets.db_url)
app.state.redis_client = build_redis(settings.redis_host)
```
Both are initialized after Vault secrets are fetched (DB URL contains the password).
Redis client is closed in the lifespan exit: `await app.state.redis_client.aclose()`.

**Single `AppError` exception handler:**
```python
@app.exception_handler(AppError)
async def app_error_handler(request, exc) -> JSONResponse:
    request_id = str(uuid4())
    logger.error("app_error", code=exc.code, message=exc.message, request_id=request_id)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message, "request_id": request_id}
    )
```
Every domain error returns a structured JSON with `error` (machine code), `message`
(human readable), and `request_id` (for log correlation). Users never see a stack trace.

**Catch-all handler for unhandled exceptions:**
```python
@app.exception_handler(Exception)
async def unhandled_error_handler(request, exc) -> JSONResponse:
    logger.exception("unhandled_error", request_id=request_id)
    return JSONResponse(status_code=500, content={...})
```
Every uncaught exception is logged with the `request_id`. The log line includes the
full traceback (via `logger.exception`). The response reveals nothing about the
internal error.

**New routers included:**
```python
app.include_router(health_router)        # GET /health
app.include_router(auth_router)          # POST /auth/register, /auth/login, GET /auth/me
app.include_router(conversations_router) # CRUD /conversations, /conversations/{id}/messages
```

---

## Architecture Decisions Made in This Phase

**D-no-fastapi-users: Why skip the fastapi-users library?**
`fastapi-users` forces you to inherit from `FastAPIUsersBase`, use its `UserCreate`/
`UserRead`/`UserUpdate` schemas, and register its router factory. Our `User` ORM model
already exists from Phase 1. Integrating fastapi-users would require either replacing
our ORM model or maintaining two parallel user representations. Custom JWT auth with
`passlib` + `python-jose` achieves the same functional requirements (registration,
login, JWT, roles, Vault-sourced signing key) with no framework coupling.

**D-domain-exceptions: Why a `code` string on every exception?**
Clients should not parse `message` strings to understand the error type — messages
can change. A stable machine-readable `code` (e.g., `"not_found"`, `"conflict"`)
lets the frontend handle errors predictably regardless of message wording.

**D-no-auto-audit: Why is audit logging done explicitly in services, not via SQLAlchemy events?**
SQLAlchemy `after_flush` events fire for all model changes. A generic audit event
handler has no way to know *why* a change happened (was it an admin action? a cascaded
delete? a background job?). Explicit `audit_repo.log(db, action="delete_conversation")`
in the service provides the `action` string and the `actor_id` in context.

**D-request-id: Why generate a fresh UUID per request instead of using a trace ID?**
The trace ID (Langfuse) is for the LLM call tree. The request ID is for HTTP error
correlation. They're separate concerns. A 404 on `GET /conversations/abc` doesn't
generate a Langfuse trace — there's nothing to trace. But it still needs a correlatable
ID for the error log. `request_id = str(uuid4())` gives us that cheaply.

---

## Acceptance Criteria (Phase 2-T)

### Unit tests (no Docker required)

- [ ] `pytest tests/test_phase2_auth.py -v`
  - `test_register_creates_user_and_returns_token`
  - `test_register_duplicate_email_raises_conflict`
  - `test_login_valid_credentials_returns_token`
  - `test_login_invalid_password_raises_auth_error`
  - `test_get_me_returns_user_from_token`
  - `test_missing_auth_header_returns_401`

- [ ] `pytest tests/test_phase2_crud.py -v`
  - `test_create_conversation`
  - `test_list_conversations_returns_only_own`
  - `test_delete_conversation_writes_audit_log`
  - `test_delete_other_users_conversation_raises_permission_denied`
  - `test_add_message_to_conversation`
  - `test_list_messages_chronological_order`

- [ ] `pytest tests/test_phase2_redaction.py -v`
  - `test_openai_key_is_redacted`
  - `test_github_token_is_redacted`
  - `test_password_in_log_is_redacted`
  - `test_clean_text_is_unchanged`

- [ ] `pytest tests/test_phase2_exceptions.py -v`
  - `test_not_found_returns_404`
  - `test_permission_denied_returns_403`
  - `test_authentication_error_returns_401`
  - `test_conflict_returns_409`
  - `test_tool_failure_returns_502`
  - `test_unhandled_exception_returns_500_no_traceback`
