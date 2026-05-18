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
    """Reject (default) or log-and-pass (audit-only) on tenant-allowlist miss.

    Log payload omits full claims to keep PII (email/upn/name) out of logs —
    only the tid + sub + the MCP method being attempted are recorded. That's
    enough for a security operator to correlate without storing PII.
    """
    log_kwargs = {
        "tid": tid,
        "sub": token_claims.get("sub"),
        "method": context.method,
        "allowed_tenants": allowed_tenants,
    }
    if audit_only:
        logger.warning("auth.tenant_denied_audit_only_passing_through", **log_kwargs)
        return await call_next(context)
    logger.warning("auth.tenant_denied", **log_kwargs)
    raise TenantNotAllowedError(
        f"Tenant {tid!r} is not on the allowlist for this MCP server"
    )
