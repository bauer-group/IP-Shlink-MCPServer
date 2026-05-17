"""
Google Workspace auth provider.

GoogleProvider is the simple case - OAuth client id/secret plus an optional
domain allowlist. The `hd` claim is Google's hosted-domain marker for
Workspace accounts; personal Gmail accounts have no `hd` and are rejected
whenever an allowlist is configured.
"""

from __future__ import annotations

from typing import Any

import structlog

from config import Settings

logger = structlog.stdlib.get_logger("bg-shlink-mcp.auth.google")


def build_google_provider(settings: Settings) -> Any:
    from fastmcp.server.auth.providers.google import GoogleProvider

    if not settings.google_client_id or not settings.google_client_secret:
        raise ValueError("Google provider requires client_id + client_secret")

    kwargs: dict[str, Any] = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret.get_secret_value(),
        "base_url": str(settings.public_base_url),
        "required_scopes": ["openid", "email", "profile"],
    }
    if settings.google_allowed_domains:
        kwargs["allowed_domains"] = settings.google_allowed_domains

    provider = GoogleProvider(**kwargs)
    logger.info(
        "auth.google_configured",
        allowed_domains=settings.google_allowed_domains or None,
    )
    return provider
