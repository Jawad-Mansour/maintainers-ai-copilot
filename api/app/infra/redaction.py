"""Redaction layer — strips secrets before any log line, trace span, or memory write.

Patterns are documented and justified in SECURITY.md.
A dedicated test (test_phase2_redaction.py) asserts no fake key leaks through.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI API keys
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "[REDACTED_OPENAI_KEY]"),
    # GitHub personal access tokens (classic + fine-grained)
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"ghs_[a-zA-Z0-9]{36}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{82}"), "[REDACTED_GITHUB_TOKEN]"),
    # Passwords in key=value / key: value form
    (re.compile(r"(?i)password\s*[=:]\s*\S+"), "password=[REDACTED_PASSWORD]"),
    # Bearer JWT tokens in Authorization headers
    (
        re.compile(r"Bearer [A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]+"),
        "Bearer [REDACTED_JWT]",
    ),
    # MinIO / AWS style secret keys (40-char base64)
    (re.compile(r"(?i)secret[_-]?key\s*[=:]\s*\S{20,}"), "secret_key=[REDACTED_SECRET]"),
]


def redact(text: str) -> str:
    """Apply all redaction patterns to text. Safe to call on any string."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
