"""Tests for the Shlink Settings (a bg-mcpcore BaseMcpSettings subclass).

Covers the per-AUTH_MODE inbound credential validation (``validate_provider_auth``)
layered on bg-mcpcore's universal fail-closed invariants, the always-required
outbound API-key guard, and the CSV-list parsing for the Entra/Google fields.
"""

from __future__ import annotations

import pytest

from config import AuthMode, Settings

# ── valid construction (every inbound mode stays configurable) ────────────────


def test_none_mode_dev_constructs(base_none_env) -> None:  # type: ignore[no-untyped-def]
    settings = Settings()
    assert settings.auth_mode is AuthMode.NONE


def test_entra_single_constructs(base_entra_env) -> None:  # type: ignore[no-untyped-def]
    settings = Settings()
    assert settings.auth_mode is AuthMode.ENTRA_SINGLE
    assert settings.entra_api_scopes == ["access_as_user"]  # default preserved


def test_entra_multi_constructs(base_entra_env) -> None:  # type: ignore[no-untyped-def]
    base_entra_env.setenv("AUTH_MODE", "entra-multi")
    base_entra_env.setenv("ENTRA_ALLOWED_TENANTS", "tenant-a, tenant-b")
    settings = Settings()
    assert settings.auth_mode is AuthMode.ENTRA_MULTI
    assert settings.entra_allowed_tenants == ["tenant-a", "tenant-b"]


def test_google_constructs(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("AUTH_MODE", "google")
    base_none_env.setenv("AUTH_JWT_SIGNING_KEY", "f" * 64)
    base_none_env.setenv("GOOGLE_CLIENT_ID", "gid")
    base_none_env.setenv("GOOGLE_CLIENT_SECRET", "gsecret")
    base_none_env.setenv("GOOGLE_ALLOWED_DOMAINS", "example.com, foo.org")
    settings = Settings()
    assert settings.auth_mode is AuthMode.GOOGLE
    assert settings.google_allowed_domains == ["example.com", "foo.org"]


def test_oidc_discovery_constructs(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("AUTH_MODE", "oidc")
    base_none_env.setenv("AUTH_JWT_SIGNING_KEY", "f" * 64)
    base_none_env.setenv("OIDC_DISCOVERY_URL", "https://idp.example.com/.well-known/openid-configuration")
    base_none_env.setenv("OIDC_CLIENT_ID", "cid")
    base_none_env.setenv("OIDC_CLIENT_SECRET", "secret")
    settings = Settings()
    assert settings.auth_mode is AuthMode.OIDC


# ── fail-closed invariants (core + provider) ─────────────────────────────────


def test_none_in_production_rejected(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="forbidden in production"):
        Settings()


def test_api_key_required(base_none_env) -> None:  # type: ignore[no-untyped-def]
    # The static X-Api-Key is required in every mode, independent of inbound auth.
    base_none_env.delenv("SHLINK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SHLINK_API_KEY is required"):
        Settings()


def test_api_key_changeme_rejected(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("SHLINK_API_KEY", "CHANGE_ME_please")
    with pytest.raises(ValueError, match="CHANGE_ME"):
        Settings()


def test_entra_without_creds_rejected(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("AUTH_MODE", "entra-single")
    base_none_env.setenv("AUTH_JWT_SIGNING_KEY", "f" * 64)
    with pytest.raises(ValueError, match="AUTH_MODE=entra-single"):
        Settings()


def test_google_without_creds_rejected(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("AUTH_MODE", "google")
    base_none_env.setenv("AUTH_JWT_SIGNING_KEY", "f" * 64)
    with pytest.raises(ValueError, match="AUTH_MODE=google"):
        Settings()


def test_oidc_without_endpoints_rejected(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("AUTH_MODE", "oidc")
    base_none_env.setenv("AUTH_JWT_SIGNING_KEY", "f" * 64)
    base_none_env.setenv("OIDC_CLIENT_ID", "cid")
    base_none_env.setenv("OIDC_CLIENT_SECRET", "secret")
    with pytest.raises(ValueError, match="OIDC_DISCOVERY_URL"):
        Settings()


def test_active_mode_requires_jwt_signing_key(base_none_env) -> None:  # type: ignore[no-untyped-def]
    base_none_env.setenv("AUTH_MODE", "entra-single")
    base_none_env.setenv("ENTRA_CLIENT_ID", "cid")
    base_none_env.setenv("ENTRA_CLIENT_SECRET", "secret")
    base_none_env.setenv("ENTRA_TENANT_ID", "tid")
    # No AUTH_JWT_SIGNING_KEY -> bg-mcpcore's core invariant fails.
    with pytest.raises(ValueError, match="AUTH_JWT_SIGNING_KEY"):
        Settings()


def test_redis_mode_requires_storage_key(base_entra_env) -> None:  # type: ignore[no-untyped-def]
    # Redis-backed OAuth state must not be stored unencrypted (core invariant).
    base_entra_env.setenv("AUTH_REDIS_URL", "redis://redis:6379/0")
    with pytest.raises(ValueError, match="AUTH_STORAGE_ENCRYPTION_KEY"):
        Settings()


def test_unknown_auth_mode_rejected(base_none_env) -> None:  # type: ignore[no-untyped-def]
    # The StrEnum keeps AUTH_MODE a closed set — no silent unauthenticated boot.
    base_none_env.setenv("AUTH_MODE", "totally-made-up")
    with pytest.raises(ValueError):
        Settings()
