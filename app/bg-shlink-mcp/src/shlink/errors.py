"""
Shlink errors - typed exceptions mapped from Shlink's RFC 7807 problem+json bodies.

A typed exception hierarchy keeps caller code clean: tools can `except ShlinkNotFound`
without inspecting status codes, and the MCP serializer turns each subclass into a
deterministic error response for the AI client.
"""

from __future__ import annotations

from typing import Any


class ShlinkError(Exception):
    """Base class for every Shlink-originating failure."""

    status_code: int | None = None
    problem_type: str | None = None

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        problem_type: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if problem_type is not None:
            self.problem_type = problem_type
        self.body: dict[str, Any] = body or {}

    def __str__(self) -> str:
        bits = [self.message]
        if self.status_code:
            bits.append(f"(HTTP {self.status_code})")
        if self.problem_type:
            bits.append(f"[{self.problem_type}]")
        return " ".join(bits)


class ShlinkAuthError(ShlinkError):
    """401/403 - the X-Api-Key was rejected or lacks permission."""


class ShlinkNotFound(ShlinkError):
    """404 - the resource (short URL, tag, domain) does not exist."""


class ShlinkValidationError(ShlinkError):
    """400/422 - the request payload failed Shlink's validation."""


class ShlinkConflict(ShlinkError):
    """409 - duplicate slug, tag rename collision, etc."""


class ShlinkRateLimited(ShlinkError):
    """429 - upstream Shlink rate-limit hit."""


class ShlinkServerError(ShlinkError):
    """5xx - upstream Shlink is unhealthy."""


class ShlinkTransportError(ShlinkError):
    """Network failure - connection refused, DNS, timeout, TLS."""


# Map RFC 7807 `type` URIs (Shlink-documented) to our exception classes.
# When the body omits `type`, we fall back to status-code routing in client.py.
PROBLEM_TYPE_TO_EXC: dict[str, type[ShlinkError]] = {
    "INVALID_ARGUMENT": ShlinkValidationError,
    "INVALID_SHORTCODE": ShlinkNotFound,
    "INVALID_SHORT_URL_DELETION": ShlinkConflict,
    "INVALID_API_KEY": ShlinkAuthError,
    "INVALID_AUTHORIZATION": ShlinkAuthError,
    "DOMAIN_NOT_FOUND": ShlinkNotFound,
    "TAG_NOT_FOUND": ShlinkNotFound,
    "TAG_CONFLICT": ShlinkConflict,
    "NON_UNIQUE_SLUG": ShlinkConflict,
}


def from_status(status: int, *, body: dict[str, Any] | None = None, message: str | None = None) -> ShlinkError:
    """Pick the most specific exception class for an HTTP status."""
    body = body or {}
    problem_type = body.get("type")
    detail = (
        body.get("detail")
        or body.get("title")
        or message
        or f"Shlink request failed with HTTP {status}"
    )

    # 1) Explicit problem-type wins.
    if isinstance(problem_type, str):
        leaf = problem_type.rsplit("/", 1)[-1]
        exc_cls = PROBLEM_TYPE_TO_EXC.get(leaf)
        if exc_cls:
            return exc_cls(detail, status_code=status, problem_type=leaf, body=body)

    # 2) Fall back to status-code routing.
    if status in (401, 403):
        return ShlinkAuthError(detail, status_code=status, body=body)
    if status == 404:
        return ShlinkNotFound(detail, status_code=status, body=body)
    if status == 409:
        return ShlinkConflict(detail, status_code=status, body=body)
    if status in (400, 422):
        return ShlinkValidationError(detail, status_code=status, body=body)
    if status == 429:
        return ShlinkRateLimited(detail, status_code=status, body=body)
    if 500 <= status < 600:
        return ShlinkServerError(detail, status_code=status, body=body)
    return ShlinkError(detail, status_code=status, body=body)
