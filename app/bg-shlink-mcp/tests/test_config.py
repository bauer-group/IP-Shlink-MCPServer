"""
Tests for src/config.py - the Pydantic Settings model and AUTH_MODE validators.

Each auth mode gets its own happy-path + missing-field cases. The placeholder
check (CHANGE_ME_*) gets its own test because it's a security-critical guard.
"""

from __future__ import annotations

import pytest

from config import AuthMode, Environment, Settings


# ── happy paths ──────────────────────────────────────────────────────────────


def test_minimal_dev_settings_parse(valid_base_env):
    s = Settings()
    assert s.environment is Environment.DEVELOPMENT
    assert s.auth_mode is AuthMode.NONE
    assert s.shlink_api_key.get_secret_value() == valid_base_env["SHLINK_API_KEY"]
    assert s.shlink_api_base.endswith("/rest/v3")


def test_entra_single_happy_path(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "entra-single")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c-id")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "c-secret")
    monkeypatch.setenv("ENTRA_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    s = Settings()
    assert s.auth_mode is AuthMode.ENTRA_SINGLE
    assert s.entra_client_id == "c-id"


def test_entra_multi_happy_path(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "entra-multi")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c-id")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "c-secret")
    monkeypatch.setenv("ENTRA_TENANT_ID", "organizations")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "aaa, bbb,ccc")
    s = Settings()
    assert s.auth_mode is AuthMode.ENTRA_MULTI
    assert s.entra_allowed_tenants == ["aaa", "bbb", "ccc"]


def test_google_happy_path(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "bauer-group.com,example.com")
    s = Settings()
    assert s.auth_mode is AuthMode.GOOGLE
    assert "bauer-group.com" in s.google_allowed_domains


def test_oidc_happy_path_with_discovery(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv(
        "OIDC_DISCOVERY_URL",
        "https://auth.example.com/.well-known/openid-configuration",
    )
    monkeypatch.setenv("OIDC_CLIENT_ID", "oidc-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "oidc-secret")
    s = Settings()
    assert s.auth_mode is AuthMode.OIDC
    assert s.oidc_discovery_url.startswith("https://")


def test_oidc_happy_path_with_explicit_uris(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_AUTH_URI", "https://auth.example.com/oauth/authorize")
    monkeypatch.setenv("OIDC_TOKEN_URI", "https://auth.example.com/oauth/token")
    monkeypatch.setenv("OIDC_JWKS_URI", "https://auth.example.com/oauth/jwks")
    monkeypatch.setenv("OIDC_CLIENT_ID", "oidc-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "oidc-secret")
    s = Settings()
    assert s.auth_mode is AuthMode.OIDC


# ── negative paths ───────────────────────────────────────────────────────────


def test_production_with_auth_none_is_rejected(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "none")
    with pytest.raises(Exception) as exc:
        Settings()
    assert "AUTH_MODE=none is forbidden in production" in str(exc.value)


def test_changeme_placeholder_rejected(monkeypatch, valid_base_env):
    monkeypatch.setenv("SHLINK_API_KEY", "CHANGE_ME_SHLINK_API_KEY")
    with pytest.raises(Exception) as exc:
        Settings()
    assert "SHLINK_API_KEY" in str(exc.value)


def test_entra_missing_client_id_rejected(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "entra-single")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "c-secret")
    monkeypatch.setenv("ENTRA_TENANT_ID", "t-id")
    # ENTRA_CLIENT_ID deliberately omitted
    with pytest.raises(Exception) as exc:
        Settings()
    assert "ENTRA_CLIENT_ID" in str(exc.value)


def test_google_missing_secret_rejected(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    with pytest.raises(Exception) as exc:
        Settings()
    assert "GOOGLE_CLIENT_SECRET" in str(exc.value)


def test_oidc_missing_endpoints_rejected(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_CLIENT_ID", "oidc-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "oidc-secret")
    # No discovery URL and no explicit URIs
    with pytest.raises(Exception) as exc:
        Settings()
    assert "OIDC_DISCOVERY_URL" in str(exc.value) or "OIDC_AUTH_URI" in str(exc.value)


def test_jwt_signing_key_required_when_auth_enabled(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "entra-single")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c-id")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "c-secret")
    monkeypatch.setenv("ENTRA_TENANT_ID", "t-id")
    monkeypatch.setenv("AUTH_JWT_SIGNING_KEY", "")  # empty
    with pytest.raises(Exception) as exc:
        Settings()
    assert "AUTH_JWT_SIGNING_KEY" in str(exc.value)


def test_redis_requires_encryption_key(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("AUTH_REDIS_URL", "redis://redis:6379/0")
    # No AUTH_STORAGE_ENCRYPTION_KEY
    with pytest.raises(Exception) as exc:
        Settings()
    assert "AUTH_STORAGE_ENCRYPTION_KEY" in str(exc.value)


# ── parsing helpers ──────────────────────────────────────────────────────────


def test_csv_list_parses_trimmed(monkeypatch, valid_base_env):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "entra-multi")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c-id")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "c-secret")
    monkeypatch.setenv("ENTRA_TENANT_ID", "organizations")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "  one ,, two ,three  ")
    s = Settings()
    assert s.entra_allowed_tenants == ["one", "two", "three"]


def test_shlink_api_base_appends_rest_v3(monkeypatch, valid_base_env):
    monkeypatch.setenv("SHLINK_URL", "http://shlink:8080/")
    s = Settings()
    assert s.shlink_api_base == "http://shlink:8080/rest/v3"
