"""
Microsoft Entra ID auth provider (single- and multi-tenant).

Both modes use FastMCP's AzureProvider. The single-tenant case is the simple
default; the multi-tenant case requires a post-issuance allowlist check on
the `tid` claim - without it, Microsoft would happily mint tokens for *any*
tenant on Earth and let them call our MCP.
"""

from __future__ import annotations

from typing import Any

import structlog

from config import AuthMode, Settings

logger = structlog.stdlib.get_logger("bg-shlink-mcp.auth.entra")

# Standard delegated scopes Entra needs for OIDC + refresh tokens.
_DEFAULT_SCOPES: tuple[str, ...] = ("openid", "profile", "email", "offline_access")


def build_entra_provider(settings: Settings) -> Any:
    """
    Return a configured AzureProvider instance.

    For multi-tenant: caller is responsible for adding the tenant allowlist
    middleware (see `enforce_tenant_allowlist`); the provider itself only
    validates JWT signatures via the multi-tenant JWKS endpoint.
    """
    from fastmcp.server.auth.providers.azure import AzureProvider

    if not settings.entra_client_id or not settings.entra_client_secret or not settings.entra_tenant_id:
        # Defensive - Pydantic should have caught this already.
        raise ValueError("Entra provider requires client_id, client_secret, tenant_id")

    scopes = list(_DEFAULT_SCOPES) + list(settings.entra_extra_scopes)
    tenant_id = settings.entra_tenant_id

    if settings.auth_mode is AuthMode.ENTRA_MULTI and tenant_id not in ("common", "organizations", "consumers"):
        # Operator gave a single tenant GUID but set multi-mode - warn loudly.
        logger.warning(
            "auth.entra_multi_with_specific_tenant",
            tenant_id=tenant_id,
            hint="set ENTRA_TENANT_ID=organizations or common for true multi-tenant",
        )

    provider = AzureProvider(
        client_id=settings.entra_client_id,
        client_secret=settings.entra_client_secret.get_secret_value(),
        tenant_id=tenant_id,
        base_url=str(settings.public_base_url),
        required_scopes=scopes,
    )

    logger.info(
        "auth.entra_configured",
        mode=settings.auth_mode.value,
        tenant_id=tenant_id,
        allowed_tenants=settings.entra_allowed_tenants or None,
        scopes=scopes,
    )
    return provider


def is_tenant_allowed(token_claims: dict[str, Any], allowed_tenants: list[str]) -> bool:
    """
    Verify the JWT `tid` claim against the allowlist.

    Returns True if the allowlist is empty (single-tenant scenarios) OR if
    the token's tid is in the list. False otherwise.

    Intended to be called from a FastMCP middleware/dependency that already
    has the decoded JWT claims available.
    """
    if not allowed_tenants:
        return True
    tid = token_claims.get("tid") or token_claims.get("tenant_id")
    if not tid:
        return False
    return tid in allowed_tenants
