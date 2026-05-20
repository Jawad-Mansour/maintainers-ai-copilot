"""Structlog configuration — structured JSON logging with trace ID injection.

Every log line carries a trace_id so logs and Langfuse traces are joinable.
The redaction processor runs on every log line before it leaves the service.
"""

from __future__ import annotations

import logging

import structlog

from app.infra.redaction import redact


def _redacting_processor(
    logger: object, method: str, event_dict: dict[str, object]
) -> dict[str, object]:
    if "event" in event_dict and isinstance(event_dict["event"], str):
        event_dict["event"] = redact(event_dict["event"])
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact(value)
    return event_dict


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            _redacting_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.types.FilteringBoundLogger:
    return structlog.get_logger(name)


def bind_trace_id(trace_id: str) -> None:
    """Bind a trace_id to the current context so all log lines carry it."""
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def clear_trace_id() -> None:
    structlog.contextvars.clear_contextvars()
