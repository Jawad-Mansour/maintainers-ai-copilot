# SECURITY.md — Redaction and Secret Handling

## Threat Model

The application processes GitHub issues that may contain API keys, passwords, and tokens pasted in bug reports. It also logs LLM prompts and tool call arguments that may include user-provided secrets. The redaction layer prevents these from persisting in:

- Structured log lines (stdout JSON)
- Langfuse trace spans (LLM inputs/outputs)
- Long-term memory entries written to pgvector

---

## Redaction Patterns

Implemented in `api/app/infra/redaction.py`. Applied as a structlog processor on **every log line** before it leaves the service (`_redacting_processor` in `api/app/infra/observability.py`).

### Pattern 1 — OpenAI API Keys

```python
re.compile(r"sk-[a-zA-Z0-9]{20,}")  →  "[REDACTED_OPENAI_KEY]"
```

**Why:** OpenAI API keys always start with `sk-` followed by 20+ alphanumeric characters. The `20,` lower bound is deliberately permissive — current keys are 48–51 characters, but the pattern must tolerate future format changes without missing keys. False positives (other `sk-` prefixed tokens) are acceptable: over-redaction is safer than under-redaction.

**Threat addressed:** Users occasionally paste their own API keys into issue bodies ("I reproduced this with key sk-..."). The key would otherwise appear verbatim in LLM input logged to Langfuse.

### Pattern 2 — GitHub Personal Access Tokens (Classic)

```python
re.compile(r"ghp_[a-zA-Z0-9]{36}")  →  "[REDACTED_GITHUB_TOKEN]"
```

**Why:** GitHub classic PATs have a fixed format: `ghp_` prefix + exactly 36 alphanumeric characters. The fixed length (36) makes this a zero-false-positive pattern — nothing else matches it.

**Threat addressed:** GitHub PATs are the most common credential pasted into issue reports ("I ran this with GITHUB_TOKEN=ghp_...").

### Pattern 3 — GitHub Server-to-Server Tokens

```python
re.compile(r"ghs_[a-zA-Z0-9]{36}")  →  "[REDACTED_GITHUB_TOKEN]"
```

**Why:** GitHub Actions installation tokens use `ghs_` prefix + 36 characters. Same fixed-format logic as pattern 2. Separate pattern to distinguish token types in logs (both map to the same replacement).

**Threat addressed:** CI workflows that paste environment variables into issue comments.

### Pattern 4 — GitHub Fine-Grained PATs

```python
re.compile(r"github_pat_[a-zA-Z0-9_]{82}")  →  "[REDACTED_GITHUB_TOKEN]"
```

**Why:** GitHub fine-grained PATs (introduced 2022) use `github_pat_` prefix + 82 alphanumeric/underscore characters. Fixed format, zero false positives.

**Threat addressed:** Users migrating from classic PATs to fine-grained tokens — both formats must be covered.

### Pattern 5 — Passwords in Key-Value Form

```python
re.compile(r"(?i)password\s*[=:]\s*\S+")  →  "password=[REDACTED_PASSWORD]"
```

**Why:** Passwords appear in logs as `password=mysecret` (URL query strings, connection strings) or `password: mysecret` (YAML, config files). The `(?i)` flag catches `Password`, `PASSWORD`, `db_password`, etc. `\S+` matches any non-whitespace run — deliberately greedy to avoid truncating multi-character passwords.

**Threat addressed:** PostgreSQL connection strings (`postgresql://user:password=...`) and application config dumps that expose raw passwords.

### Pattern 6 — Bearer JWT Tokens

```python
re.compile(r"Bearer [A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]+")
→  "Bearer [REDACTED_JWT]"
```

**Why:** JWTs are three Base64URL-encoded segments separated by dots. The pattern requires all three segments (header.payload.signature), which eliminates false positives from dot-separated version numbers or file paths. This covers both our own JWTs and third-party JWTs users may paste.

**Threat addressed:** Maintainers sometimes include their auth token in bug reports. If a JWT appeared in an LLM prompt and that prompt was logged to Langfuse, an attacker with Langfuse access could use the token.

### Pattern 7 — MinIO / AWS Secret Keys

```python
re.compile(r"(?i)secret[_-]?key\s*[=:]\s*\S{20,}")  →  "secret_key=[REDACTED_SECRET]"
```

**Why:** AWS secret access keys are 40 characters; MinIO uses the same format. The pattern matches any `secret_key =` or `secretkey:` or `SECRET-KEY =` form followed by 20+ non-whitespace characters. The `20,` lower bound avoids redacting short configuration values accidentally named `secret_key`.

**Threat addressed:** MinIO/S3 connection strings and infrastructure config dumps that may appear in error messages or stack traces logged by the application.

---

## Where Redaction Runs

```
Every structlog log line
  │
  └─ _redacting_processor (observability.py)
       │  applied in structlog processor chain BEFORE JSONRenderer
       │  runs redact() on event field + all string-typed fields
       └─ Output: clean JSON to stdout
```

Langfuse trace inputs/outputs are passed through `redact()` before being sent to the Langfuse SDK in `chat_service.py`.

Memory writes (`write_memory` tool) pass content through `redact()` before storing in pgvector.

---

## Secret Storage — Vault

All runtime secrets are stored in HashiCorp Vault at `secret/data/copilot`. The API never reads from environment variables directly — it fetches from Vault at startup via `app/infra/vault.py`.

The `.env` file holds only:
- `VAULT_ADDR` — Vault endpoint
- `VAULT_TOKEN` — Vault root token (dev mode only)

**The `.env` file is git-ignored and must never be committed.**

In production, `VAULT_TOKEN` would be replaced with a short-lived AppRole credential. The dev root token is acceptable only for the course demo environment.

---

## What Is NOT Redacted (by design)

- Issue titles and bodies not matching the patterns above — these are the application's working data
- Classifier labels (`bug`, `feature`, `docs`, `question`) — not sensitive
- Source URLs for retrieved chunks — these are public GitHub URLs
- User email addresses in log lines — not considered secret for this application's threat model; email is the primary user identifier

---

## Security Test

`tests/test_phase2_redaction.py` asserts that no fake credentials pass through `redact()` unmodified:

```python
def test_openai_key_redacted():
    # Construct a fake key at runtime so static scanners don't flag this file
    fake_key = "sk-" + "a" * 48
    result = redact(f"key: {fake_key}")
    assert "[REDACTED_OPENAI_KEY]" in result
    assert fake_key not in result

def test_github_token_redacted():
    result = redact(f"token: ghp_{'a' * 36}")
    assert "[REDACTED_GITHUB_TOKEN]" in result

def test_bearer_jwt_redacted():
    # Three-segment dot-separated Base64URL string
    fake_jwt = "Bearer " + ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJ1c2VyIn0", "SflKxwRJSMeKKF2QT4fw"])
    result = redact(fake_jwt)
    assert "[REDACTED_JWT]" in result
```
