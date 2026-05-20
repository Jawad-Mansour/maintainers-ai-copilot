"""Phase 2 — Unit tests for secret redaction."""

from __future__ import annotations

from api.app.infra.redaction import redact


def test_redacts_openai_key() -> None:
    text = "key=sk-abcdefghijklmnopqrstuvwxyz123456"  # gitleaks:allow
    assert "[REDACTED_OPENAI_KEY]" in redact(text)
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in redact(text)


def test_redacts_github_pat_classic() -> None:
    text = "token ghp_" + "A" * 36
    result = redact(text)
    assert "[REDACTED_GITHUB_TOKEN]" in result
    assert "ghp_" not in result


def test_redacts_github_pat_server() -> None:
    text = "auth ghs_" + "B" * 36
    result = redact(text)
    assert "[REDACTED_GITHUB_TOKEN]" in result


def test_redacts_password_equals_form() -> None:
    text = "password=supersecret123"
    result = redact(text)
    assert "supersecret123" not in result
    assert "password=[REDACTED_PASSWORD]" in result


def test_redacts_password_colon_form() -> None:
    text = "password: mysecretpassword"
    result = redact(text)
    assert "mysecretpassword" not in result


def test_redacts_bearer_jwt() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc123DEF456ghi789"  # gitleaks:allow
    text = f"Authorization: Bearer {token}"
    result = redact(text)
    assert token not in result
    assert "Bearer [REDACTED_JWT]" in result


def test_redacts_secret_key() -> None:
    text = "secret_key=abcdefghijklmnopqrstuvwxyz1234"  # gitleaks:allow
    result = redact(text)
    assert "abcdefghijklmnopqrstuvwxyz1234" not in result
    assert "[REDACTED_SECRET]" in result


def test_clean_string_unchanged() -> None:
    text = "this is a normal log message with no secrets"
    assert redact(text) == text


def test_redacts_multiple_secrets_in_one_string() -> None:
    text = "sk-abcdefghijklmnopqrstuvwxyz password=secret"
    result = redact(text)
    assert "[REDACTED_OPENAI_KEY]" in result
    assert "[REDACTED_PASSWORD]" in result
