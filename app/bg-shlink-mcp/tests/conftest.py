"""Shared pytest fixtures.

``isolate_env`` wipes all SHLINK_* / AUTH_* / OIDC_* / ENTRA_* / GOOGLE_* /
MCP_* / RATE_LIMITER_* / SENTRY_* variables before each test so the Pydantic
model parses against a known-empty environment — without this, os.environ
leakage from a CI runner (e.g. SHLINK_API_KEY in a developer's shell) makes
tests non-deterministic. The autouse ``_reset_settings_cache`` clears
bg-mcpcore's per-class settings cache so each test parses fresh.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Make the src layout importable without an install step (`from config import …`).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# A 64-char hex string (32 bytes) — structurally valid JWT signing key.
_TEST_JWT_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
# A Fernet key (44-char url-safe base64 of 32 zero bytes) — tests only.
_TEST_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

_TEST_ENV_PREFIXES = (
    "SHLINK_",
    "AUTH_",
    "OIDC_",
    "ENTRA_",
    "GOOGLE_",
    "RATE_LIMITER_",
    "SENTRY_",
    "MCP_",
    "EXTENSIONS_",
    "PUBLIC_BASE_URL",
    "ENVIRONMENT",
    "LOG_",
)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Clear bg-mcpcore's per-class settings cache so each test parses fresh."""
    from bg_mcpcore.settings import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that Settings() would otherwise pick up."""
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in _TEST_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    # Pydantic Settings reads `.env` from the CWD — cd into the tests dir so a
    # developer's local .env is never picked up.
    monkeypatch.chdir(Path(__file__).resolve().parent)


@pytest.fixture
def base_none_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A minimal AUTH_MODE=none + dev env that passes validation."""
    for key, value in {
        "ENVIRONMENT": "development",
        "PUBLIC_BASE_URL": "http://localhost:8000",
        "SHLINK_URL": "http://shlink:8080",
        "SHLINK_API_KEY": "test-shlink-api-key-deadbeef",
        "AUTH_MODE": "none",
    }.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


@pytest.fixture
def base_entra_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A minimal AUTH_MODE=entra-single env that passes validation."""
    for key, value in {
        "ENVIRONMENT": "development",
        "PUBLIC_BASE_URL": "https://mcp.example.com",
        "SHLINK_URL": "https://go.example.com",
        "SHLINK_API_KEY": "test-shlink-api-key-deadbeef",
        "AUTH_MODE": "entra-single",
        "AUTH_JWT_SIGNING_KEY": _TEST_JWT_KEY,
        "ENTRA_CLIENT_ID": "test-client-id",
        "ENTRA_CLIENT_SECRET": "test-client-secret",
        "ENTRA_TENANT_ID": "test-tenant-id",
    }.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


@pytest.fixture
def jwt_key() -> str:
    return _TEST_JWT_KEY


@pytest.fixture
def fernet_key() -> str:
    return _TEST_FERNET_KEY
