"""
Microsoft Entra ID auth provider (single- and multi-tenant).

Both modes use FastMCP's AzureProvider. The single-tenant case is the simple
default; the multi-tenant case requires a post-issuance allowlist check on
the `tid` claim - without it, Microsoft would happily mint tokens for *any*
tenant on Earth and let them call our MCP.

Scope model (Azure-specific - do not conflate the two lists):

- `required_scopes` (= settings.entra_api_scopes): custom API scopes you
  EXPOSED in the Azure app registration under "Expose an API". These appear
  in the `scp` claim of access tokens issued for *your* API and are what
  AzureProvider actually validates. At least one non-OIDC scope is mandatory
  by FastMCP 3.x.
- `additional_authorize_scopes` (= OIDC defaults + settings.entra_extra_scopes):
  scopes requested DURING authorization but never validated on incoming
  tokens. Microsoft Graph delegated permissions (e.g. `User.Read`) belong
  here - they end up in tokens audience-scoped to Graph, not our API.
  `offline_access` is auto-added by AzureProvider; we include the other
  OIDC scopes here so the consent screen still shows them.
"""

from __future__ import annotations

from typing import Any

import structlog

from config import AuthMode, Settings

logger = structlog.stdlib.get_logger("bg-shlink-mcp.auth.entra")

# OIDC scopes are requested during authorization (so the consent screen lists
# them and we get an id_token) but they're never present in the access token's
# scp claim - hence not validatable and not part of required_scopes.
_OIDC_AUTHORIZE_SCOPES: tuple[str, ...] = ("openid", "profile", "email")


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

    api_scopes = list(settings.entra_api_scopes)
    if not api_scopes:
        raise ValueError(
            "ENTRA_API_SCOPES must contain at least one custom API scope "
            "(e.g. 'access_as_user'). Expose it in the Azure app registration "
            "under 'Expose an API' and grant admin consent."
        )

    # Authorization-time scopes: OIDC + any extras (Graph delegated permissions
    # such as User.Read). These are surfaced to the user on the consent screen
    # but are NOT validated against incoming tokens.
    additional_authorize_scopes = list(_OIDC_AUTHORIZE_SCOPES) + list(settings.entra_extra_scopes)

    tenant_id = settings.entra_tenant_id

    if settings.auth_mode is AuthMode.ENTRA_MULTI and tenant_id not in ("common", "organizations", "consumers"):
        # Operator gave a single tenant GUID but set multi-mode - warn loudly.
        logger.warning(
            "auth.entra_multi_with_specific_tenant",
            tenant_id=tenant_id,
            hint="set ENTRA_TENANT_ID=organizations or common for true multi-tenant",
        )

    from .client_storage import build_client_storage

    provider = AzureProvider(
        client_id=settings.entra_client_id,
        client_secret=settings.entra_client_secret.get_secret_value(),
        tenant_id=tenant_id,
        base_url=str(settings.public_base_url),
        required_scopes=api_scopes,
        additional_authorize_scopes=additional_authorize_scopes,
        # Persistent encrypted storage — without this FastMCP falls back to
        # platformdirs which is ephemeral inside containers, causing all
        # OAuth state to vanish on restart. See auth/client_storage.py.
        client_storage=build_client_storage(settings),
        jwt_signing_key=settings.auth_jwt_signing_key.get_secret_value(),
    )

    logger.info(
        "auth.entra_configured",
        mode=settings.auth_mode.value,
        tenant_id=tenant_id,
        allowed_tenants=settings.entra_allowed_tenants or None,
        api_scopes=api_scopes,
        additional_authorize_scopes=additional_authorize_scopes,
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
