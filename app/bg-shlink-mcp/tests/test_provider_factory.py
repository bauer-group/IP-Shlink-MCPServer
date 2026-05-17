"""
Tests for src/auth/provider_factory.py - dispatch + defensive guards.

We monkeypatch each concrete provider builder so we never need a real IdP
to run these tests.
"""

from __future__ import annotations

import pytest

from config import AuthMode, Environment, Settings


class _Stub:
    def __init__(self, label: str) -> None:
        self.label = label


def _build_settings(monkeypatch, *, auth_mode: str, environment: str = "production", **extra: str) -> Settings:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("SHLINK_URL", "http://shlink:8080")
    monkeypatch.setenv("SHLINK_API_KEY", "test-key-deadbeef")
    monkeypatch.setenv("AUTH_MODE", auth_mode)
    monkeypatch.setenv(
        "AUTH_JWT_SIGNING_KEY",
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    return Settings()


def test_factory_returns_none_for_dev_no_auth(monkeypatch):
    settings = _build_settings(monkeypatch, auth_mode="none", environment="development")
    from auth.provider_factory import build_auth_provider

    assert build_auth_provider(settings) is None


def test_factory_dispatches_entra_single(monkeypatch):
    settings = _build_settings(
        monkeypatch,
        auth_mode="entra-single",
        ENTRA_CLIENT_ID="c-id",
        ENTRA_CLIENT_SECRET="c-secret",
        ENTRA_TENANT_ID="t-id",
    )

    import auth.entra as entra_mod
    from auth import provider_factory

    monkeypatch.setattr(entra_mod, "build_entra_provider", lambda s: _Stub("entra"))
    monkeypatch.setattr(provider_factory, "AuthMode", AuthMode)
    result = provider_factory.build_auth_provider(settings)
    assert isinstance(result, _Stub) and result.label == "entra"


def test_factory_dispatches_google(monkeypatch):
    settings = _build_settings(
        monkeypatch,
        auth_mode="google",
        GOOGLE_CLIENT_ID="g-id",
        GOOGLE_CLIENT_SECRET="g-secret",
    )

    import auth.google as google_mod
    from auth import provider_factory

    monkeypatch.setattr(google_mod, "build_google_provider", lambda s: _Stub("google"))
    result = provider_factory.build_auth_provider(settings)
    assert isinstance(result, _Stub) and result.label == "google"


def test_factory_dispatches_oidc(monkeypatch):
    settings = _build_settings(
        monkeypatch,
        auth_mode="oidc",
        OIDC_DISCOVERY_URL="https://auth.example.com/.well-known/openid-configuration",
        OIDC_CLIENT_ID="oidc-id",
        OIDC_CLIENT_SECRET="oidc-secret",
    )

    import auth.generic_oidc as oidc_mod
    from auth import provider_factory

    monkeypatch.setattr(oidc_mod, "build_generic_oidc_provider", lambda s: _Stub("oidc"))
    result = provider_factory.build_auth_provider(settings)
    assert isinstance(result, _Stub) and result.label == "oidc"


def test_factory_refuses_none_in_production_defensively(monkeypatch):
    """
    Pydantic should catch this first, but if a caller constructs Settings
    manually the factory must still refuse.
    """
    # We bypass Settings() because Pydantic would reject this before the factory.
    class StubSettings:
        auth_mode = AuthMode.NONE
        environment = Environment.PRODUCTION

    from auth.provider_factory import build_auth_provider

    with pytest.raises(RuntimeError, match="AUTH_MODE=none requires ENVIRONMENT=development"):
        build_auth_provider(StubSettings())  # type: ignore[arg-type]
