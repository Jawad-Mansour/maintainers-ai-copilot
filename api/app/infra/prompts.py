"""Prompt loader — reads .md templates from api/prompts/ with module-level caching."""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """Load and cache a prompt template from api/prompts/{name}.md."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{name}' not found at {path}")
    return path.read_text(encoding="utf-8").strip()
