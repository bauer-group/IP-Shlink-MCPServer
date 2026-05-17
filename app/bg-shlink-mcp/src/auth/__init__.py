"""Authentication providers - inbound OAuth 2.1 + PKCE via FastMCP."""

from .provider_factory import build_auth_provider

__all__ = ["build_auth_provider"]
