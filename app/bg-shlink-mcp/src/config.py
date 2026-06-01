"""Shlink MCP Server configuration — a bg-mcpcore ``BaseMcpSettings`` subclass.

The cross-cutting settings (environment, transport, MCP identity, auth
persistence, OIDC, rate limiting, observability) all come from
``bg_mcpcore.BaseMcpSettings``. This subclass adds only the Shlink-specific
backend + the inbound-IdP credential fields (Entra / Google), narrows
``auth_mode`` to the Shlink-supported set, and enforces the per-mode credential
requirements. The universal fail-closed invariants — none-in-production, the JWT
signing key, the Fernet storage key — already run in core, *before*
``validate_provider_auth``.

Two trust boundaries:
- inbound  (AI client -> MCP)   - OAuth 2.1 + PKCE via Entra / Google / OIDC
- outbound (MCP -> Shlink API)  - a static ``X-Api-Key`` header (resolved by the
                                  profile's outbound resolver from SHLINK_API_KEY)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from bg_mcpcore import BaseMcpSettings
from bg_mcpcore.settings import get_settings as _core_get_settings
from bg_mcpcore.settings.enums import Environment as Environment  # re-exported for callers
from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import NoDecode


class AuthMode(StrEnum):
    # Microsoft Entra ID — single tenant (the BAUER GROUP production mode).
    ENTRA_SINGLE = "entra-single"
    # Microsoft Entra ID — multi tenant (gated by ENTRA_ALLOWED_TENANTS).
    ENTRA_MULTI = "entra-multi"
    # Google Workspace (optional GOOGLE_ALLOWED_DOMAINS hosted-domain gate).
    GOOGLE = "google"
    # Any standards-compliant OIDC IdP (Authentik, Keycloak, Zitadel, ...).
    OIDC = "oidc"
    # Development only: no auth on the MCP endpoint.
    NONE = "none"


def _split_csv(raw: object) -> list[str]:
    """Parse a comma-separated env value into a list. Tolerates whitespace."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value().strip())
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


class Settings(BaseMcpSettings):
    """Shlink-specific settings on top of the shared bg-mcpcore base."""

    # Narrow the generic ``auth_mode`` (a free str on the base) to Shlink's set.
    auth_mode: AuthMode = AuthMode.NONE

    # This server's consent-screen name (the base leaves it required).
    mcp_display_name: str = "BAUER GROUP Shlink"

    # ── Shlink backend ─────────────────────────────────────────────────────────
    # NB: the backend base URL + the OpenAPI spec source are resolved by the
    # profile (backend.base_url = ${env:SHLINK_URL}; spec.source =
    # ${env:SHLINK_OPENAPI_URL:-file:///app/openapi/shlink.json}). ``shlink_url``
    # stays here only as a typed default for tooling; the outbound resolver reads
    # SHLINK_API_KEY from the env directly. The CHANGE_ME guard below keeps the
    # boot-time rejection of an unconfigured key.
    shlink_url: HttpUrl = Field(
        default="http://shlink:8080",  # type: ignore[assignment]
        description="Base URL of the Shlink REST API (without /rest/v3).",
    )
    shlink_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Shlink API key — has full read/write authority over Shlink.",
    )

    # ── Microsoft Entra ID (single + multi) ────────────────────────────────────
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None
    entra_tenant_id: str | None = None
    entra_allowed_tenants: Annotated[list[str], NoDecode] = Field(default_factory=list)
    entra_api_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["access_as_user"],
        description=(
            "Custom API scopes exposed under 'Expose an API' in the Azure app "
            "registration. Unprefixed names; AzureProvider prefixes them with "
            "api://<client_id>/ before validating the token's scp claim. Must "
            "contain at least one non-OIDC scope."
        ),
    )
    entra_extra_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Upstream scopes requested during authorization but NOT validated "
            "(e.g. Microsoft Graph delegated permissions like 'User.Read')."
        ),
    )

    # ── Google Workspace ───────────────────────────────────────────────────────
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_allowed_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ── Validators ─────────────────────────────────────────────────────────────

    @field_validator(
        "entra_allowed_tenants",
        "entra_api_scopes",
        "entra_extra_scopes",
        "google_allowed_domains",
        mode="before",
    )
    @classmethod
    def _parse_csv_list(cls, value: object) -> list[str]:
        return _split_csv(value)

    @field_validator("shlink_api_key", mode="after")
    @classmethod
    def _api_key_required(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not secret or secret.startswith("CHANGE_ME"):
            raise ValueError(
                "SHLINK_API_KEY is required and must not contain the CHANGE_ME placeholder"
            )
        return value

    def validate_provider_auth(self) -> None:
        """Per-mode inbound credential checks (core invariants already ran).

        Outbound auth (the static X-Api-Key) is required in every mode and is
        guarded by ``_api_key_required`` above, independent of the inbound mode.
        """
        if self.auth_mode in (AuthMode.ENTRA_SINGLE, AuthMode.ENTRA_MULTI):
            missing = [
                name
                for name in ("entra_client_id", "entra_client_secret", "entra_tenant_id")
                if not _has_value(getattr(self, name))
            ]
            if missing:
                raise ValueError(
                    f"{', '.join(n.upper() for n in missing)} required for AUTH_MODE={self.auth_mode}"
                )

        elif self.auth_mode is AuthMode.GOOGLE:
            missing = [
                name
                for name in ("google_client_id", "google_client_secret")
                if not _has_value(getattr(self, name))
            ]
            if missing:
                raise ValueError(
                    f"{', '.join(n.upper() for n in missing)} required for AUTH_MODE=google"
                )

        elif self.auth_mode is AuthMode.OIDC:
            has_discovery = bool(self.oidc_discovery_url)
            has_explicit = all(
                _has_value(getattr(self, name))
                for name in ("oidc_auth_uri", "oidc_token_uri", "oidc_jwks_uri")
            )
            if not (has_discovery or has_explicit):
                raise ValueError(
                    "AUTH_MODE=oidc requires OIDC_DISCOVERY_URL "
                    "or all of OIDC_AUTH_URI / OIDC_TOKEN_URI / OIDC_JWKS_URI"
                )
            missing = [
                name
                for name in ("oidc_client_id", "oidc_client_secret")
                if not _has_value(getattr(self, name))
            ]
            if missing:
                raise ValueError(f"{', '.join(n.upper() for n in missing)} required for AUTH_MODE=oidc")


def get_settings(force_reload: bool = False) -> Settings:
    """Lazy singleton (delegates to bg-mcpcore's per-class settings cache)."""
    return _core_get_settings(Settings, force_reload=force_reload)


__all__ = ["AuthMode", "Environment", "Settings", "get_settings"]
