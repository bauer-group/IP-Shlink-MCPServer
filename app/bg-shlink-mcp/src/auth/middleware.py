"""
Tenant allowlist middleware for AUTH_MODE=entra-multi.

The AzureProvider validates JWT signatures against Microsoft's multi-tenant
JWKS, but it does NOT check WHICH tenant the token was issued for. In
multi-tenant mode, that means anyone whose Entra admin consented to our
app in their own tenant gets a valid token — exactly what multi-tenant
auth is designed to do, but rarely what an operator running an internal
MCP wants.

This middleware closes the gap by reading the `tid` claim from the
authenticated token and gating it against `ENTRA_ALLOWED_TENANTS`. When
the list is empty, the middleware is a no-op (single-tenant scenarios,
intentional any-tenant deployments).

Wiring lives in `server.py:build_app` — the middleware is registered only
when the auth mode is `entra-multi` AND `entra_allowed_tenants` is set.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from auth.entra import is_tenant_allowed

logger = structlog.stdlib.get_logger("bg-shlink-mcp.auth.middleware")


class TenantNotAllowedError(PermissionError):
    """Raised when a request's token tenant is not on the allowlist.

    PermissionError because we want MCP clients to see this as an authz
    failure (not transport or input-validation). FastMCP serialises raised
    exceptions in middleware as JSON-RPC error responses with the
    exception class name in the error payload.
    """


class TenantAllowlistMiddleware(Middleware):
    """Enforce ENTRA_ALLOWED_TENANTS on every authenticated request.

    The check runs in `on_request` so it fires for every method —
    `tools/call`, `resources/read`, `prompts/get`, `tools/list`, etc. —
    instead of needing to be re-added on each hook.
    """

    def __init__(self, allowed_tenants: list[str], *, audit_only: bool = False) -> None:
        self._allowed_tenants = list(allowed_tenants)
        self._audit_only = audit_only

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        token = get_access_token()
        # No token = unauthenticated request. AzureProvider should have
        # already rejected it before middleware runs; nothing to gate.
        if token is None:
            return await call_next(context)

        if is_tenant_allowed(token.claims, self._allowed_tenants):
            return await call_next(context)

        # ── User-implemented policy starts here ────────────────────────────
        tid = token.claims.get("tid")
        return await _handle_disallowed_tenant(
            tid=tid,
            allowed_tenants=self._allowed_tenants,
            audit_only=self._audit_only,
            context=context,
            call_next=call_next,
            token_claims=token.claims,
        )


async def _handle_disallowed_tenant(
    *,
    tid: str | None,
    allowed_tenants: list[str],
    audit_only: bool,
    context: MiddlewareContext[Any],
    call_next: CallNext[Any, Any],
    token_claims: dict[str, Any],
) -> Any:
    """Decide what happens when a token's tid is NOT on the allowlist.

    TODO (you): implement the rejection policy. Trade-offs to consider:

    1. Hard reject vs. audit-only
       - `audit_only=True` should log and pass through (`return await call_next(context)`).
         Useful during staged rollouts: you can wire the middleware in, watch the logs
         for who would have been denied, THEN flip to enforcement.
       - `audit_only=False` should reject (`raise TenantNotAllowedError(...)`).
         This is the production posture.

    2. What goes in the log payload?
       The `tid` is the bare minimum. Useful additions: `sub` (which user),
       `azp` (which client app), `context.method` (what they tried to do).
       Avoid logging the entire `token_claims` — it may contain email/upn
       and other PII that BG security standards prefer kept out of logs.

    3. Log level
       Audit-only path: `logger.warning` (operator wants to see these but
       they're not yet incidents). Enforcement path: `logger.warning` for
       expected churn / `logger.error` for "this should never happen" —
       your call based on threat model.

    Expected length: 5-10 lines. Reference: structlog calls already use
    kwargs (e.g. `logger.warning("event.name", tid=tid, allowed=allowed_tenants)`).
    """
    # TODO: replace this placeholder with your policy.
    raise NotImplementedError(
        "Tenant rejection policy not implemented. "
        "See _handle_disallowed_tenant docstring in auth/middleware.py."
    )
