"""Named-entity recognition pipeline using spaCy + custom EntityRuler patterns.

Uses spacy.blank("en") — no statistical model required.
All entities are captured by deterministic EntityRuler patterns:
  VERSION   — semantic version strings like "1.5.2", "2.0.0rc1"
  PACKAGE   — known Python/data-science package names
  EXCEPTION — Python exception class names (ending in Error / Exception)
  FILEPATH  — Python file paths like "pandas/core/frame.py"
  FUNCTION  — method/function calls like "DataFrame.merge("
"""

from __future__ import annotations

import spacy

# ─── Custom patterns ──────────────────────────────────────────────────────────

_KNOWN_PACKAGES = [
    "pandas",
    "numpy",
    "matplotlib",
    "scipy",
    "sklearn",
    "scikit-learn",
    "torch",
    "pytorch",
    "tensorflow",
    "keras",
    "xarray",
    "dask",
    "polars",
    "pyarrow",
    "fastparquet",
    "sqlalchemy",
    "psycopg2",
    "requests",
    "httpx",
    "pydantic",
    "fastapi",
    "uvicorn",
    "celery",
    "redis",
    "boto3",
]

_PHRASE_PATTERNS: list[dict] = [{"label": "PACKAGE", "pattern": name} for name in _KNOWN_PACKAGES]

_TOKEN_PATTERNS: list[dict] = [
    # VERSION — single token (e.g. "version 2.0.1" where tokenizer keeps it whole)
    {
        "label": "VERSION",
        "pattern": [{"TEXT": {"REGEX": r"^\d+\.\d+(\.\d+)?(\.dev\d+|[ab]\d+|rc\d+)?$"}}],
    },
    # VERSION — three-part split (e.g. pandas==2.0.1 → "2" "." "0" "." "1")
    # spaCy blank("en") splits on "." between digits as infixes
    {
        "label": "VERSION",
        "pattern": [
            {"TEXT": {"REGEX": r"^\d+$"}},
            {"TEXT": "."},
            {"TEXT": {"REGEX": r"^\d+$"}},
            {"TEXT": "."},
            {"TEXT": {"REGEX": r"^\d+[a-z0-9]*$"}},
        ],
    },
    # VERSION — two-part split (e.g. "Python 3.11" → "3" "." "11")
    {
        "label": "VERSION",
        "pattern": [
            {"TEXT": {"REGEX": r"^\d+$"}},
            {"TEXT": "."},
            {"TEXT": {"REGEX": r"^\d+[a-z0-9]*$"}},
        ],
    },
    # Python exception classes
    {
        "label": "EXCEPTION",
        "pattern": [{"TEXT": {"REGEX": r"^[A-Z]\w*(Error|Exception|Warning)$"}}],
    },
    # Python file paths (e.g. frame.py or pandas/core/frame.py when kept as one token)
    {
        "label": "FILEPATH",
        "pattern": [{"TEXT": {"REGEX": r"^[\w/\\]+\.py$"}}],
    },
    # Method/function calls — two tokens because spaCy always splits "(" as a suffix
    # e.g. "DataFrame.merge(" → token "DataFrame.merge" + token "("
    {
        "label": "FUNCTION",
        "pattern": [{"TEXT": {"REGEX": r"^\w+\.\w+$"}}, {"TEXT": "("}],
    },
]


class NERPipeline:
    def __init__(self) -> None:
        # blank pipeline — no statistical model needed; EntityRuler covers all our labels
        self._nlp = spacy.blank("en")
        ruler = self._nlp.add_pipe("entity_ruler", config={"overwrite_ents": True})
        ruler.add_patterns(_PHRASE_PATTERNS + _TOKEN_PATTERNS)  # type: ignore[arg-type]

    def extract(self, text: str) -> list[dict[str, str]]:
        """Return deduplicated list of {text, label} dicts."""
        doc = self._nlp(text[:2000])  # cap for latency
        seen: set[tuple[str, str]] = set()
        entities: list[dict[str, str]] = []
        for ent in doc.ents:
            key = (ent.text, ent.label_)
            if key not in seen:
                seen.add(key)
                entities.append({"text": ent.text, "label": ent.label_})
        return entities
