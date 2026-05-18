"""Tests for the entra-multi tenant allowlist middleware.

These tests exercise the middleware mechanics — token extraction, the
allow path, the policy-dispatch path — without depending on the user-
defined rejection policy (which is intentionally a TODO in production
code). When you implement `_handle_disallowed_tenant`, extend
`test_disallowed_tid_invokes_policy` to assert your actual behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from auth import middleware as mw_module
from auth.middleware import TenantAllowlistMiddleware, TenantNotAllowedError


@dataclass
class _StubToken:
    """Minimal stand-in for fastmcp.server.auth.AccessToken."""
    claims: dict[str, Any]


class _StubContext:
    """Stand-in for MiddlewareContext. The middleware only reads .method."""
    method: str | None = "tools/call"


async def _passthrough(_ctx: Any) -> str:
    return "ok"


# ── Allow path ──────────────────────────────────────────────────────────────


async def test_no_token_passes_through(monkeypatch):
    """Anonymous request (no auth provider) hits call_next unchanged."""
    monkeypatch.setattr(mw_module, "get_access_token", lambda: None)
    middleware = TenantAllowlistMiddleware(allowed_tenants=["tenant-a"])
    result = await middleware.on_request(_StubContext(), _passthrough)
    assert result == "ok"


async def test_tid_in_allowlist_passes_through(monkeypatch):
    """A token whose tid is on the list reaches the downstream handler."""
    monkeypatch.setattr(
        mw_module,
        "get_access_token",
        lambda: _StubToken(claims={"tid": "tenant-a", "sub": "u1"}),
    )
    middleware = TenantAllowlistMiddleware(allowed_tenants=["tenant-a", "tenant-b"])
    result = await middleware.on_request(_StubContext(), _passthrough)
    assert result == "ok"


async def test_empty_allowlist_passes_everything(monkeypatch):
    """An empty list means 'no enforcement' — matches is_tenant_allowed's contract."""
    monkeypatch.setattr(
        mw_module,
        "get_access_token",
        lambda: _StubToken(claims={"tid": "any-tenant"}),
    )
    middleware = TenantAllowlistMiddleware(allowed_tenants=[])
    result = await middleware.on_request(_StubContext(), _passthrough)
    assert result == "ok"


# ── Reject / policy-dispatch path ───────────────────────────────────────────


async def test_disallowed_tid_hard_rejects_by_default(monkeypatch):
    """Default policy: raise TenantNotAllowedError, deny the operation."""
    monkeypatch.setattr(
        mw_module,
        "get_access_token",
        lambda: _StubToken(claims={"tid": "tenant-evil", "sub": "u1"}),
    )
    middleware = TenantAllowlistMiddleware(allowed_tenants=["tenant-a"])
    with pytest.raises(TenantNotAllowedError, match="tenant-evil"):
        await middleware.on_request(_StubContext(), _passthrough)


async def test_disallowed_tid_audit_only_passes_through(monkeypatch):
    """audit_only=True: log the denial but let the request through.

    Useful during staged rollouts — wire the middleware in, watch logs for
    who would have been denied, then flip audit_only=False once confident.
    """
    monkeypatch.setattr(
        mw_module,
        "get_access_token",
        lambda: _StubToken(claims={"tid": "tenant-evil", "sub": "u1"}),
    )
    middleware = TenantAllowlistMiddleware(
        allowed_tenants=["tenant-a"],
        audit_only=True,
    )
    result = await middleware.on_request(_StubContext(), _passthrough)
    assert result == "ok"


async def test_missing_tid_claim_is_disallowed(monkeypatch):
    """No tid claim at all = treated as not-on-allowlist (is_tenant_allowed returns False)."""
    monkeypatch.setattr(
        mw_module,
        "get_access_token",
        lambda: _StubToken(claims={"sub": "u1"}),  # no tid
    )
    middleware = TenantAllowlistMiddleware(allowed_tenants=["tenant-a"])
    with pytest.raises(TenantNotAllowedError):
        await middleware.on_request(_StubContext(), _passthrough)


# ── Type sanity ─────────────────────────────────────────────────────────────


def test_tenant_not_allowed_is_permission_error():
    """TenantNotAllowedError must be a PermissionError so MCP clients see authz failure."""
    assert issubclass(TenantNotAllowedError, PermissionError)
