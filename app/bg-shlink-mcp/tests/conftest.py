"""
Shared pytest fixtures.

`isolate_env` wipes all SHLINK_* / AUTH_* / OIDC_* / ENTRA_* / GOOGLE_*
variables before each test so the Pydantic model parses against a known
empty environment. Without this, `os.environ` leakage from CI runners
(e.g. SHLINK_API_KEY in a developer's shell) makes tests non-deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

# Make the src layout importable without an install step.
_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))


_TEST_ENV_PREFIXES = (
    "SHLINK_",
    "AUTH_",
    "OIDC_",
    "ENTRA_",
    "GOOGLE_",
    "RATE_LIMITER_",
    "SENTRY_",
    "MCP_",
    "PUBLIC_BASE_URL",
    "ENVIRONMENT",
    "LOG_",
)


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that Settings() would otherwise pick up."""
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in _TEST_ENV_PREFIXES) or key == "ENVIRONMENT":
            monkeypatch.delenv(key, raising=False)
    # Pydantic Settings reads `.env` if present in CWD - point it at /dev/null.
    monkeypatch.chdir(Path(__file__).resolve().parent)


@pytest.fixture
def valid_base_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A minimal valid env that satisfies the Pydantic validators."""
    env = {
        "ENVIRONMENT": "development",
        "PUBLIC_BASE_URL": "http://localhost:8000",
        "SHLINK_URL": "http://shlink:8080",
        "SHLINK_API_KEY": "test-shlink-api-key-deadbeef",
        "AUTH_MODE": "none",
        "AUTH_JWT_SIGNING_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env
