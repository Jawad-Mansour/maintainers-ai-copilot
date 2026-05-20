# Phase 2-T — API Core Tests

**Status:** ✅ All passed (2026-05-20)
**Scope:** 5 test files, 39 tests — auth service, conversation/message CRUD service,
HTTP route integration, exception hierarchy, and redaction processor.

---

## Goal

Verify that authentication works end-to-end (JWT creation, JWT decoding, bcrypt
hashing, duplicate detection, inactive account handling), conversation and message CRUD
enforces ownership rules correctly, the exception hierarchy maps to the right HTTP
status codes, and the redaction processor strips every secret pattern from log output.
All 39 tests run without Docker — every database and network call is replaced with
a targeted mock, so the tests are fast, deterministic, and useful as specification
documentation.

---

## Why Mock-Based Tests Here

Phase 2 introduces five distinct subsystems: JWT handling, bcrypt, ownership logic,
exception mapping, and redaction. Each can be wrong independently. Using real services
for these tests would make failures ambiguous — does the test fail because the JWT
is wrong, or because the database connection timed out? Pure unit tests pin exactly
one thing at a time. If `test_register_raises_conflict_if_email_taken` fails, the
conflict detection logic is broken. The database is not involved and never can be.

HTTP route tests (Phase 2-T also covers these via `test_phase2_crud_routes.py`) use
FastAPI's test client with dependency overrides, not mocks. This lets us confirm that
routes call the right services, return the right status codes, and handle the `AppError`
exception handler correctly — again without a real database.

---

## Test Files

### `tests/test_phase2_auth.py` — 7 tests

This file tests both the JWT handler (as a pure Python unit) and the auth service
(with mocked repository calls).

**JWT handler tests (no service, no mock, pure Python):**

`test_create_and_decode_token` — Creates a token with `create_access_token()`, then
decodes it with `decode_access_token()`. Asserts that the decoded payload contains the
correct `sub` (user ID) and `role`. This proves the round-trip works: a token you create
can be decoded back to the same identity. Any regression in JWT encoding (wrong algorithm,
wrong claim name) would fail this test.

`test_decode_raises_on_wrong_key` — Encodes a token with key A, attempts to decode
with key B, asserts that `AuthenticationError` is raised. This confirms that HS256
signature verification is active. If someone swaps the signing key (e.g., key rotation
bug), existing tokens must be rejected rather than silently accepted.

`test_decode_raises_on_garbage_token` — Passes a non-JWT string (`"not.a.valid.jwt"`)
to `decode_access_token`, asserts `AuthenticationError`. Guards against injection of
malformed authorization headers.

**Auth service tests (mocked repository):**

`test_register_raises_conflict_if_email_taken` — Mocks `user_repo.get_by_email` to
return a user object (simulating a duplicate email). Asserts `ConflictError` is raised
with a message matching `"already registered"`. Proves the duplicate-check guard works
without needing a real database.

`test_register_creates_user_and_returns_token` — Mocks `get_by_email` to return `None`
(no duplicate) and `user_repo.create` to return a fake user with a valid UUID and role.
Asserts the returned `LoginResponse` has a non-empty `access_token` and `token_type == "bearer"`.
Proves registration is connected end-to-end from HTTP → service → JWT.

`test_login_raises_on_bad_password` — Mocks the user lookup (returns a valid user) but
mocks `_pwd.verify` to return `False`. Asserts `AuthenticationError`. This is the test
that proves the password verification step is not bypassed.

`test_login_raises_when_user_not_found` — Mocks `get_by_email` to return `None`. Asserts
`AuthenticationError`. Prevents a timing-attack distinction between "user not found" and
"wrong password" — both must raise the same error class.

---

### `tests/test_phase2_crud.py` — 7 tests

This file tests the conversation and message service layer. All repository calls are
mocked. The focus is on ownership enforcement and atomic audit logging.

`test_create_conversation_returns_out` — Mocks `conversation_repo.create` to return a
fake conversation object. Asserts the returned `ConversationOut` has the correct `id`
and `user_id`. Confirms the service correctly validates and maps the ORM object to a
Pydantic response.

`test_list_conversations_filters_by_user` — Mocks `conversation_repo.list_by_user`
to return a list of two conversations. Asserts the result list has length 2. Proves
that the service calls the user-filtered repo method rather than the global one.

`test_delete_raises_not_found_when_missing` — Mocks `conversation_repo.get` to return
`None`. Asserts `NotFoundError`. Verifies the guard runs before attempting a delete on a
non-existent row.

`test_delete_raises_permission_denied_for_wrong_user` — Mocks `get` to return a
conversation owned by `OTHER_ID`. Calls `delete_conversation` with `OWNER_ID`. Asserts
`PermissionDenied`. This is the critical ownership test: if this fails, one user can
delete another user's conversations.

`test_delete_succeeds_for_owner` — Mocks all three calls: `get` (returns the owner's
conversation), `delete_by_id` (no-op), `audit_repo.log` (no-op). Asserts `db.commit()`
was called exactly once. Verifies the atomic commit contract: audit log + delete happen
in a single transaction.

`test_add_message_raises_permission_denied_for_wrong_user` — Same ownership check, but
for `add_message`. Asserts `PermissionDenied` when the conversation belongs to a
different user. Without this, one user could POST messages into another user's conversation.

`test_list_messages_raises_not_found_when_missing` — Mocks `get` to return `None`.
Asserts `NotFoundError` from `list_messages`. Verifies that listing messages from a
non-existent conversation raises a clean error rather than returning an empty list.

---

### `tests/test_phase2_crud_routes.py` — 6 tests

These are HTTP-level tests using FastAPI's `ASGITransport` test client. They confirm
that routes call the correct services, return the correct status codes, and handle
authorization headers. A shared `client` fixture (defined in the file) overrides the
`get_db`, `get_current_user`, and `get_redis` dependencies with mock versions — the
app process runs but no real database or Vault is involved.

`test_register_returns_201` — POSTs to `/auth/register` with a valid payload. Mocks the
underlying service. Asserts HTTP 201 and a non-empty `access_token` in the response body.

`test_login_returns_200` — POSTs to `/auth/login`. Asserts HTTP 200 and a `"bearer"`
token type.

`test_me_returns_current_user` — GETs `/auth/me` with the `fake_user` injected via
`get_current_user` override. Asserts HTTP 200 and that the response `email` matches the
fake user.

`test_create_conversation_returns_201` — POSTs to `/conversations`. Mocks service. Asserts
HTTP 201 and a valid UUID in the response body.

`test_list_conversations_returns_200` — GETs `/conversations`. Asserts HTTP 200 and a
list response.

`test_delete_conversation_returns_204` — DELETEs `/conversations/{id}`. Asserts HTTP 204
(no content). Confirms that a successful delete returns no body.

---

### `tests/test_phase2_exceptions.py` — 10 tests

Pure Python tests. No HTTP, no mocks, no async. This file verifies every class in the
exception hierarchy independently.

Each exception class is tested for:
1. Correct `status_code` attribute (e.g., `NotFoundError` has 404, not 400 or 500)
2. Correct `code` string attribute (e.g., `PermissionDenied.code == "permission_denied"`)
3. Inheritance from `AppError` (so the single exception handler catches all of them)

`test_all_errors_are_app_error` — Instantiates every custom exception class and
asserts `isinstance(exc, AppError)`. If someone adds a new exception class that doesn't
inherit from `AppError`, the single exception handler in `main.py` would not catch it,
producing an unhandled 500 instead of the expected status code. This test is a
regression guard for that entire category of mistake.

`test_app_error_is_catchable_as_exception` — Asserts `isinstance(AppError(...), Exception)`.
Proves the hierarchy is correctly rooted.

---

### `tests/test_phase2_redaction.py` — 9 tests

Tests the `redact()` function from `api/app/infra/redaction.py` directly. No logging,
no HTTP, no mocks. Each test passes a string containing a known secret pattern and
asserts that the output does not contain the original secret but does contain the
`[REDACTED]` placeholder.

**Patterns tested:**

`test_redacts_openai_key` — Input: `"key: sk-abc123abc123abc123abc123"`. Output must
not contain `"sk-abc123..."`. Confirms the OpenAI key regex fires.

`test_redacts_github_pat_classic` — Input contains `"ghp_"` followed by 36 alphanumeric
characters. Must be redacted.

`test_redacts_github_pat_server` — Input contains `"ghs_"` (server-to-server token).
Must be redacted. This is a different prefix from `ghp_` but same format.

`test_redacts_password_equals_form` — Input: `"password=supersecret"`. Must be redacted.
Tests the case-insensitive `password=value` pattern.

`test_redacts_password_colon_form` — Input: `"password: supersecret"`. Tests the
`password: value` variant (YAML-style).

`test_redacts_bearer_jwt` — Input: `"Authorization: Bearer eyJhbGciOiJIUzI1NiJ9..."`.
Must be redacted. Catches the case where a user pastes a curl command into a GitHub
issue and the app echoes it into a log line.

`test_redacts_secret_key` — Input: `"secret_key=miniosecretkeyvalue"`. Tests the
MinIO/AWS secret key pattern.

`test_clean_string_unchanged` — Input: a string with no secrets. Output must be
identical to input. Ensures the redactor doesn't mangle clean data.

`test_redacts_multiple_secrets_in_one_string` — Input contains two different patterns
(an OpenAI key and a password) in a single string. Both must be redacted. Confirms that
the regex is applied globally (not just to the first match).

---

## Fixtures and Test Infrastructure (`conftest.py`)

All Phase 2-T route tests rely on shared fixtures defined in `tests/conftest.py`:

| Fixture | Type | Purpose |
|---------|------|---------|
| `fake_secrets` | `VaultSecrets` | Pre-built secrets object with test values (no Vault connection) |
| `fake_user` | `UserOut` | A regular user with `role="user"` |
| `fake_admin` | `UserOut` | An admin user with `role="admin"` |
| `mock_db` | `AsyncMock` | Drop-in for `AsyncSession` — records all calls |
| `mock_redis` | `AsyncMock` | Redis client with `.get()` returning `None`, `.set()` returning `True` |
| `mock_minio` | `MagicMock` | MinIO client stub |
| `mock_modelserver` | `AsyncMock` | Modelserver with `.classify()` returning `["bug"]` |
| `mock_langfuse` | `MagicMock` | Langfuse client with trace/generation chain stubbed |

These fixtures are injected by dependency overrides into the FastAPI test app. The
critical pattern is:
```python
app.dependency_overrides[get_db] = lambda: mock_db
app.dependency_overrides[get_current_user] = lambda: fake_user
```
The FastAPI router calls the real route handlers, the real services, and the real
exception handler — only the database and Vault interactions are replaced.

---

## Issues Hit During Phase 2-T

### `SIM117` — Nested `with` statements

Ruff flagged nested `with` contexts like:
```python
with patch("...user_repo.get_by_email", return_value=None):
    with pytest.raises(AuthenticationError):
        await login(...)
```
These were collapsed into single parenthesized `with` blocks:
```python
with (
    patch("...user_repo.get_by_email", return_value=None),
    pytest.raises(AuthenticationError),
):
    await login(...)
```
This is Python 3.10+ syntax (parenthesized context managers). The project targets
Python 3.11+, so this is safe.

### `E501` — Long lines in mock patches

Several `patch()` calls with long module paths exceeded the 100-character line limit.
These were split across multiple lines using Python's implicit string continuation inside
parentheses:
```python
with patch(
    "api.app.services.auth_service.user_repo.get_by_email",
    return_value=existing_user,
):
```

---

## Pass Criteria — All Met ✅

- [x] `pytest tests/test_phase2_auth.py -v` → **7 passed**
- [x] `pytest tests/test_phase2_crud.py -v` → **7 passed**
- [x] `pytest tests/test_phase2_crud_routes.py -v` → **6 passed**
- [x] `pytest tests/test_phase2_exceptions.py -v` → **10 passed**
- [x] `pytest tests/test_phase2_redaction.py -v` → **9 passed**
- [x] **Total: 39/39 passed**

**Phase 2-T passed. Cleared to proceed to Phase 3.**
