"""
Tests for the concrete auth provider builders (Entra / Google / Generic OIDC).

We monkeypatch the FastMCP provider classes to capture the kwargs the
builders pass in - the unit-level concern is that Settings -> provider
arguments translation is correct.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from config import Settings


# ── helpers ──────────────────────────────────────────────────────────────────


def _capture_factory():
    """Return a (stub_class, captured_kwargs_dict) pair."""
    captured: dict[str, Any] = {}

    class Stub:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.kwargs = kwargs

    return Stub, captured


def _settings_with(monkeypatch, **overrides: str) -> Settings:
    defaults = {
        "ENVIRONMENT": "production",
        "PUBLIC_BASE_URL": "https://shlink-mcp.example.com",
        "SHLINK_URL": "http://shlink:8080",
        "SHLINK_API_KEY": "test-key-abc",
        "AUTH_JWT_SIGNING_KEY": "0" * 64,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
    return Settings()


# ── Entra ────────────────────────────────────────────────────────────────────


def test_entra_single_passes_tenant_and_scopes(monkeypatch):
    settings = _settings_with(
        monkeypatch,
        AUTH_MODE="entra-single",
        ENTRA_CLIENT_ID="cid",
        ENTRA_CLIENT_SECRET="csecret",
        ENTRA_TENANT_ID="t-guid-1",
        ENTRA_EXTRA_SCOPES="User.Read, Calendars.Read",
    )

    Stub, captured = _capture_factory()
    import fastmcp.server.auth.providers.azure as azure_mod

    monkeypatch.setattr(azure_mod, "AzureProvider", Stub)
    from auth.entra import build_entra_provider

    provider = build_entra_provider(settings)
    assert isinstance(provider, Stub)
    assert captured["client_id"] == "cid"
    assert captured["client_secret"] == "csecret"
    assert captured["tenant_id"] == "t-guid-1"
    assert "openid" in captured["required_scopes"]
    assert "User.Read" in captured["required_scopes"]
    assert "Calendars.Read" in captured["required_scopes"]


def test_entra_multi_passes_organizations(monkeypatch):
    settings = _settings_with(
        monkeypatch,
        AUTH_MODE="entra-multi",
        ENTRA_CLIENT_ID="cid",
        ENTRA_CLIENT_SECRET="csecret",
        ENTRA_TENANT_ID="organizations",
    )

    Stub, captured = _capture_factory()
    import fastmcp.server.auth.providers.azure as azure_mod

    monkeypatch.setattr(azure_mod, "AzureProvider", Stub)
    from auth.entra import build_entra_provider

    build_entra_provider(settings)
    assert captured["tenant_id"] == "organizations"


def test_is_tenant_allowed_blocks_missing_tid():
    from auth.entra import is_tenant_allowed

    assert is_tenant_allowed({}, ["t1", "t2"]) is False
    assert is_tenant_allowed({"tid": "t-other"}, ["t1", "t2"]) is False
    assert is_tenant_allowed({"tid": "t1"}, ["t1", "t2"]) is True


def test_is_tenant_allowed_empty_list_lets_everyone_through():
    """Empty allowlist means single-tenant scenario - skip the check."""
    from auth.entra import is_tenant_allowed

    assert is_tenant_allowed({"tid": "anything"}, []) is True


# ── Google ───────────────────────────────────────────────────────────────────


def test_google_passes_allowed_domains(monkeypatch):
    settings = _settings_with(
        monkeypatch,
        AUTH_MODE="google",
        GOOGLE_CLIENT_ID="gid",
        GOOGLE_CLIENT_SECRET="gsecret",
        GOOGLE_ALLOWED_DOMAINS="bauer-group.com, example.com",
    )

    Stub, captured = _capture_factory()
    import fastmcp.server.auth.providers.google as google_mod

    monkeypatch.setattr(google_mod, "GoogleProvider", Stub)
    from auth.google import build_google_provider

    build_google_provider(settings)
    assert captured["client_id"] == "gid"
    assert captured["client_secret"] == "gsecret"
    assert "bauer-group.com" in captured["allowed_domains"]
    assert "example.com" in captured["allowed_domains"]


def test_google_omits_allowed_domains_when_unset(monkeypatch):
    settings = _settings_with(
        monkeypatch,
        AUTH_MODE="google",
        GOOGLE_CLIENT_ID="gid",
        GOOGLE_CLIENT_SECRET="gsecret",
    )

    Stub, captured = _capture_factory()
    import fastmcp.server.auth.providers.google as google_mod

    monkeypatch.setattr(google_mod, "GoogleProvider", Stub)
    from auth.google import build_google_provider

    build_google_provider(settings)
    assert "allowed_domains" not in captured


# ── Generic OIDC ─────────────────────────────────────────────────────────────


@respx.mock
def test_oidc_discovery_uses_oidcproxy(monkeypatch):
    discovery_url = "https://auth.example.com/.well-known/openid-configuration"
    discovery_doc = {
        "issuer": "https://auth.example.com/",
        "authorization_endpoint": "https://auth.example.com/oauth/authorize",
        "token_endpoint": "https://auth.example.com/oauth/token",
        "jwks_uri": "https://auth.example.com/oauth/jwks",
        "userinfo_endpoint": "https://auth.example.com/oauth/userinfo",
    }
    respx.get(discovery_url).mock(return_value=httpx.Response(200, json=discovery_doc))

    settings = _settings_with(
        monkeypatch,
        AUTH_MODE="oidc",
        OIDC_DISCOVERY_URL=discovery_url,
        OIDC_CLIENT_ID="oid",
        OIDC_CLIENT_SECRET="osecret",
    )

    Stub, captured = _capture_factory()
    import fastmcp.server.auth.oidc_proxy as oidc_mod

    monkeypatch.setattr(oidc_mod, "OIDCProxy", Stub)
    from auth.generic_oidc import build_generic_oidc_provider

    build_generic_oidc_provider(settings)
    assert captured["config_url"] == discovery_url
    assert captured["client_id"] == "oid"
    assert captured["client_secret"] == "osecret"
    assert captured["required_scopes"] == ["openid", "profile", "email"]


@respx.mock
def test_oidc_discovery_raises_on_missing_fields(monkeypatch):
    discovery_url = "https://auth.example.com/.well-known/openid-configuration"
    respx.get(discovery_url).mock(
        return_value=httpx.Response(200, json={"issuer": "x"})  # missing required fields
    )
    settings = _settings_with(
        monkeypatch,
        AUTH_MODE="oidc",
        OIDC_DISCOVERY_URL=discovery_url,
        OIDC_CLIENT_ID="oid",
        OIDC_CLIENT_SECRET="osecret",
    )
    from auth.generic_oidc import build_generic_oidc_provider

    with pytest.raises(ValueError, match="discovery"):
        build_generic_oidc_provider(settings)


def test_oidc_explicit_uris_used_when_no_discovery(monkeypatch):
    settings = _settings_with(
        monkeypatch,
        AUTH_MODE="oidc",
        OIDC_AUTH_URI="https://auth.example.com/oauth/authorize",
        OIDC_TOKEN_URI="https://auth.example.com/oauth/token",
        OIDC_JWKS_URI="https://auth.example.com/oauth/jwks",
        OIDC_ISSUER="https://auth.example.com/",
        OIDC_CLIENT_ID="oid",
        OIDC_CLIENT_SECRET="osecret",
        OIDC_SCOPES="openid profile email groups",
    )

    Stub, captured = _capture_factory()
    import fastmcp.server.auth.oauth_proxy as proxy_mod
    import fastmcp.server.auth.providers.jwt as jwt_mod

    class _StubVerifier:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(jwt_mod, "JWTVerifier", _StubVerifier)
    monkeypatch.setattr(proxy_mod, "OAuthProxy", Stub)
    from auth.generic_oidc import build_generic_oidc_provider

    build_generic_oidc_provider(settings)
    assert captured["upstream_authorization_endpoint"] == "https://auth.example.com/oauth/authorize"
    assert "groups" in captured["valid_scopes"]
