"""Domain exception hierarchy.

All exceptions map to HTTP responses via the single handler in main.py.
Users never see a stack trace — they see a structured error with a code and request_id.
"""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, code: str, status_code: int) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, "not_found", 404)


class PermissionDenied(AppError):
    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(message, "permission_denied", 403)


class AuthenticationError(AppError):
    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message, "authentication_error", 401)


class ConflictError(AppError):
    def __init__(self, message: str = "Resource already exists") -> None:
        super().__init__(message, "conflict", 409)


class ToolFailure(AppError):
    """Raised when an external tool call (classifier, NER, etc.) fails."""

    def __init__(self, message: str = "Tool call failed") -> None:
        super().__init__(message, "tool_failure", 502)


class ValidationError(AppError):
    def __init__(self, message: str = "Validation error") -> None:
        super().__init__(message, "validation_error", 422)


class RateLimitError(AppError):
    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(message, "rate_limit", 429)
