"""Phase 2 — Unit tests for AppError hierarchy."""

from __future__ import annotations

import pytest

from app.exceptions import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDenied,
    RateLimitError,
    ToolFailure,
    ValidationError,
)


def test_not_found_error() -> None:
    err = NotFoundError("missing thing")
    assert err.status_code == 404
    assert err.code == "not_found"
    assert err.message == "missing thing"


def test_not_found_default_message() -> None:
    err = NotFoundError()
    assert "not found" in err.message.lower()


def test_permission_denied() -> None:
    err = PermissionDenied()
    assert err.status_code == 403
    assert err.code == "permission_denied"


def test_authentication_error() -> None:
    err = AuthenticationError("bad token")
    assert err.status_code == 401
    assert err.code == "authentication_error"


def test_conflict_error() -> None:
    err = ConflictError()
    assert err.status_code == 409
    assert err.code == "conflict"


def test_tool_failure() -> None:
    err = ToolFailure("modelserver down")
    assert err.status_code == 502
    assert err.code == "tool_failure"


def test_validation_error() -> None:
    err = ValidationError()
    assert err.status_code == 422
    assert err.code == "validation_error"


def test_rate_limit_error() -> None:
    err = RateLimitError()
    assert err.status_code == 429
    assert err.code == "rate_limit"


def test_all_errors_are_app_error() -> None:
    errors = [
        NotFoundError(),
        PermissionDenied(),
        AuthenticationError(),
        ConflictError(),
        ToolFailure(),
        ValidationError(),
        RateLimitError(),
    ]
    for err in errors:
        assert isinstance(err, AppError)
        assert isinstance(err, Exception)


def test_app_error_is_catchable_as_exception() -> None:
    with pytest.raises(AppError):
        raise NotFoundError("test")
