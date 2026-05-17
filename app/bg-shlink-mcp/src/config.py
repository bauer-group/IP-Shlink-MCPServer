"""
Shlink MCP Server - Configuration

Pydantic Settings loaded from environment variables. Two trust boundaries:
- inbound (AI client -> MCP) - OAuth 2.1 + PKCE via Entra / Google / OIDC
- outbound (MCP -> Shlink) - static X-Api-Key header

The model_validator below is the only thing that prevents production deployments
from silently slipping into AUTH_MODE=none. Do not relax it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


class AuthMode(StrEnum):
    ENTRA_SINGLE = "entra-single"
    ENTRA_MULTI = "entra-multi"
    GOOGLE = "google"
    OIDC = "oidc"
    NONE = "none"


def _split_csv(raw: str | list[str] | None) -> list[str]:
    """Parse a comma-separated env value into a list. Tolerates whitespace."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [item.strip() for item in raw if str(item).strip()]
    return [item.strip() for item in str(raw).split(",") if item.strip()]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── General ────────────────────────────────────────────────────────────
    environment: Environment = Environment.PRODUCTION
    public_base_url: HttpUrl = Field(
        default="http://localhost:8000",
        description="Public origin used in OAuth redirect URIs - MUST match IdP registration",
    )
    log_format: Literal["console", "json"] = "json"
    log_level: str = "INFO"

    # ── Shlink backend ─────────────────────────────────────────────────────
    shlink_url: HttpUrl = Field(
        default="http://shlink:8080",
        description="Base URL of the Shlink REST API (without /rest/v3)",
    )
    shlink_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Shlink API key - has full read/write authority over Shlink",
    )
    shlink_openapi_url: str = Field(
        default="file:///app/openapi/shlink.json",
        description=(
            "OpenAPI spec source: file://... or https://...  "
            "The default points at the spec baked into the image at build time "
            "(pinned via the SHLINK_OPENAPI_VERSION build-arg). Override to "
            "track a live source - e.g. a custom Shlink fork or a different version."
        ),
    )
    shlink_openapi_refresh_interval: int = Field(
        default=0,
        ge=0,
        description=(
            "How often (s) to refresh the spec at runtime; 0 = never. "
            "Stay at 0 for the baked-in default - the file is immutable. "
            "Only set > 0 when SHLINK_OPENAPI_URL points at a mutable remote source."
        ),
    )
    shlink_http_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Per-request timeout for outbound calls to Shlink",
    )

    # ── MCP transport ──────────────────────────────────────────────────────
    mcp_transport: Literal["streamable-http", "stdio"] = "streamable-http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = Field(default=8000, ge=1, le=65535)

    # ── Auth ───────────────────────────────────────────────────────────────
    auth_mode: AuthMode = AuthMode.NONE
    auth_jwt_signing_key: SecretStr = Field(
        default=SecretStr(""),
        description="32-byte hex key used to sign FastMCP-issued JWTs",
    )
    auth_redis_url: str | None = None
    auth_storage_encryption_key: SecretStr | None = None

    # Entra ID (single + multi)
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None
    entra_tenant_id: str | None = None
    entra_allowed_tenants: Annotated[list[str], NoDecode] = Field(default_factory=list)
    entra_extra_scopes: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Google
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_allowed_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Generic OIDC
    oidc_discovery_url: str | None = None
    oidc_issuer: str | None = None
    oidc_auth_uri: str | None = None
    oidc_token_uri: str | None = None
    oidc_jwks_uri: str | None = None
    oidc_userinfo_uri: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid profile email"
    oidc_username_claim: str = "preferred_username"

    # ── Rate limiting ──────────────────────────────────────────────────────
    rate_limiter_enabled: bool = True
    rate_limiter_requests: int = Field(default=600, ge=1)
    rate_limiter_window_seconds: int = Field(default=60, ge=1)

    # ── Observability ──────────────────────────────────────────────────────
    sentry_dsn: str | None = None
    sentry_environment: str | None = None
    sentry_traces_sample_rate: float = Field(default=0.05, ge=0.0, le=1.0)

    # ── Validators ─────────────────────────────────────────────────────────

    @field_validator(
        "entra_allowed_tenants",
        "entra_extra_scopes",
        "google_allowed_domains",
        mode="before",
    )
    @classmethod
    def _parse_csv_list(cls, value: object) -> list[str]:
        return _split_csv(value)  # type: ignore[arg-type]

    @field_validator("shlink_api_key", mode="after")
    @classmethod
    def _api_key_required(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not secret or secret.startswith("CHANGE_ME"):
            raise ValueError(
                "SHLINK_API_KEY is required and must not contain the CHANGE_ME placeholder"
            )
        return value

    @model_validator(mode="after")
    def _validate_auth(self) -> "Settings":
        """Enforce the per-mode requirements declared in the spec."""

        if self.auth_mode is AuthMode.NONE:
            if self.environment is Environment.PRODUCTION:
                raise ValueError(
                    "AUTH_MODE=none is forbidden in production "
                    "(set ENVIRONMENT=development if this is intentional)"
                )

        elif self.auth_mode in (AuthMode.ENTRA_SINGLE, AuthMode.ENTRA_MULTI):
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
                raise ValueError(
                    f"{', '.join(n.upper() for n in missing)} required for AUTH_MODE=oidc"
                )

        # Persistence: the JWT signing key must be provided whenever any auth mode is active.
        if self.auth_mode is not AuthMode.NONE:
            signing = self.auth_jwt_signing_key.get_secret_value()
            if not signing or signing.startswith("CHANGE_ME"):
                raise ValueError(
                    "AUTH_JWT_SIGNING_KEY is required and must not be a CHANGE_ME placeholder"
                )

        # Redis-backed client store requires an encryption key (no plaintext at rest).
        if self.auth_redis_url and self.auth_mode is not AuthMode.NONE:
            if not self.auth_storage_encryption_key or not self.auth_storage_encryption_key.get_secret_value():
                raise ValueError(
                    "AUTH_STORAGE_ENCRYPTION_KEY is required when AUTH_REDIS_URL is set"
                )

        return self

    # ── Convenience accessors ──────────────────────────────────────────────

    @property
    def shlink_api_base(self) -> str:
        """REST v3 base URL (Shlink convention - prepended to every request)."""
        return str(self.shlink_url).rstrip("/") + "/rest/v3"

    @property
    def is_development(self) -> bool:
        return self.environment is Environment.DEVELOPMENT


def _has_value(value: object) -> bool:
    """True if a config value is present (handles SecretStr + str + None)."""
    if value is None:
        return False
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


_settings: Settings | None = None


def get_settings(force_reload: bool = False) -> Settings:
    """Lazy singleton - parses env only once unless force_reload=True."""
    global _settings
    if _settings is None or force_reload:
        _settings = Settings()
    return _settings
