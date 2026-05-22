"""OpenAI tool schemas for the chatbot agent loop."""

from __future__ import annotations

CLASSIFY_ISSUE: dict = {
    "type": "function",
    "function": {
        "name": "classify_issue",
        "description": (
            "Classify a GitHub issue as one of: bug, feature, question, docs, performance. "
            "Call this when triaging any new issue."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The issue title and body concatenated.",
                },
            },
            "required": ["text"],
        },
    },
}

SEARCH_KNOWLEDGE_BASE: dict = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": (
            "Search the project knowledge base with hybrid semantic + keyword search. "
            "Use this to find relevant documentation, past issues, or code context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "label": {
                    "type": "string",
                    "description": (
                        "Optional corpus collection label."
                        " The knowledge base uses label='docs'."
                        " Leave unset to search the full corpus."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (1-10, default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

EXTRACT_ENTITIES: dict = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "description": (
            "Extract named entities (versions, package names, file paths, function names, "
            "exception types) from the issue text using NER."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to analyze.",
                },
            },
            "required": ["text"],
        },
    },
}

SUMMARIZE_THREAD: dict = {
    "type": "function",
    "function": {
        "name": "summarize_thread",
        "description": (
            "Summarize the current conversation thread. "
            "Useful before writing a memory or when the thread has grown long."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Optional aspect to emphasize in the summary.",
                },
            },
            "required": [],
        },
    },
}

WRITE_MEMORY: dict = {
    "type": "function",
    "function": {
        "name": "write_memory",
        "description": (
            "Save an important insight, preference, or fact about this user to long-term memory. "
            "Call this only for facts worth remembering across sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "The memory to save (1-3 sentences, third-person).",
                },
            },
            "required": ["summary"],
        },
    },
}

ALL_TOOLS: list[dict] = [
    CLASSIFY_ISSUE,
    SEARCH_KNOWLEDGE_BASE,
    EXTRACT_ENTITIES,
    SUMMARIZE_THREAD,
    WRITE_MEMORY,
]
