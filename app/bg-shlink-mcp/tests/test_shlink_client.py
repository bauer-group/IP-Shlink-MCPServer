"""
Tests for src/shlink/client.py - retries, error mapping, header injection.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from shlink.client import ShlinkClient
from shlink.errors import (
    ShlinkAuthError,
    ShlinkConflict,
    ShlinkNotFound,
    ShlinkRateLimited,
    ShlinkServerError,
    ShlinkTransportError,
    ShlinkValidationError,
)


@pytest.fixture
def base_url() -> str:
    return "http://shlink.test/rest/v3"


# ── Header injection ─────────────────────────────────────────────────────────


@respx.mock
async def test_api_key_header_is_sent(base_url):
    route = respx.get(f"{base_url}/short-urls").mock(
        return_value=httpx.Response(200, json={"shortUrls": []})
    )
    client = ShlinkClient(base_url=base_url, api_key="my-key-abc")
    try:
        await client.get("/short-urls")
    finally:
        await client.aclose()
    assert route.called
    assert route.calls.last.request.headers["X-Api-Key"] == "my-key-abc"


def test_empty_api_key_rejected():
    with pytest.raises(ValueError, match="api_key"):
        ShlinkClient(base_url="http://shlink.test", api_key="")


# ── Error mapping ────────────────────────────────────────────────────────────


@respx.mock
async def test_404_raises_not_found(base_url):
    respx.get(f"{base_url}/short-urls/abc123").mock(
        return_value=httpx.Response(
            404,
            json={"type": "INVALID_SHORTCODE", "detail": "Short code not found"},
        )
    )
    client = ShlinkClient(base_url=base_url, api_key="k", max_retries=0)
    try:
        with pytest.raises(ShlinkNotFound) as exc:
            await client.get("/short-urls/abc123")
        assert "Short code not found" in str(exc.value)
    finally:
        await client.aclose()


@respx.mock
async def test_401_raises_auth_error(base_url):
    respx.get(f"{base_url}/short-urls").mock(
        return_value=httpx.Response(401, json={"detail": "bad key"})
    )
    client = ShlinkClient(base_url=base_url, api_key="k", max_retries=0)
    try:
        with pytest.raises(ShlinkAuthError):
            await client.get("/short-urls")
    finally:
        await client.aclose()


@respx.mock
async def test_409_raises_conflict(base_url):
    respx.post(f"{base_url}/short-urls").mock(
        return_value=httpx.Response(
            409,
            json={"type": "NON_UNIQUE_SLUG", "detail": "slug taken"},
        )
    )
    client = ShlinkClient(base_url=base_url, api_key="k", max_retries=0)
    try:
        with pytest.raises(ShlinkConflict):
            await client.post("/short-urls", json={"longUrl": "https://x"})
    finally:
        await client.aclose()


@respx.mock
async def test_422_raises_validation_error(base_url):
    respx.post(f"{base_url}/short-urls").mock(
        return_value=httpx.Response(422, json={"detail": "longUrl required"})
    )
    client = ShlinkClient(base_url=base_url, api_key="k", max_retries=0)
    try:
        with pytest.raises(ShlinkValidationError):
            await client.post("/short-urls", json={})
    finally:
        await client.aclose()


# ── Retry behaviour ──────────────────────────────────────────────────────────


@respx.mock
async def test_5xx_retries_then_succeeds(base_url):
    route = respx.get(f"{base_url}/short-urls").mock(
        side_effect=[
            httpx.Response(503, json={"detail": "temporarily down"}),
            httpx.Response(503, json={"detail": "still down"}),
            httpx.Response(200, json={"shortUrls": []}),
        ]
    )
    client = ShlinkClient(
        base_url=base_url, api_key="k", max_retries=3, backoff_base=0.0, backoff_max=0.0
    )
    try:
        response = await client.get("/short-urls")
        assert response.status_code == 200
        assert route.call_count == 3
    finally:
        await client.aclose()


@respx.mock
async def test_5xx_retries_exhausted_raises(base_url):
    respx.get(f"{base_url}/short-urls").mock(
        return_value=httpx.Response(502, json={"detail": "bad gateway"})
    )
    client = ShlinkClient(
        base_url=base_url, api_key="k", max_retries=2, backoff_base=0.0, backoff_max=0.0
    )
    try:
        with pytest.raises(ShlinkServerError):
            await client.get("/short-urls")
    finally:
        await client.aclose()


@respx.mock
async def test_429_retries_with_retry_after(base_url):
    route = respx.get(f"{base_url}/visits").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"}),
            httpx.Response(200, json={"visits": []}),
        ]
    )
    client = ShlinkClient(
        base_url=base_url, api_key="k", max_retries=3, backoff_base=0.0, backoff_max=0.1
    )
    try:
        response = await client.get("/visits")
        assert response.status_code == 200
        assert route.call_count == 2
    finally:
        await client.aclose()


@respx.mock
async def test_429_exhausted_raises_rate_limited(base_url):
    respx.get(f"{base_url}/visits").mock(
        return_value=httpx.Response(429, json={"detail": "rate limit"})
    )
    client = ShlinkClient(
        base_url=base_url, api_key="k", max_retries=1, backoff_base=0.0, backoff_max=0.0
    )
    try:
        with pytest.raises(ShlinkRateLimited):
            await client.get("/visits")
    finally:
        await client.aclose()


@respx.mock
async def test_network_error_retried_then_raises(base_url):
    respx.get(f"{base_url}/short-urls").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    client = ShlinkClient(
        base_url=base_url, api_key="k", max_retries=2, backoff_base=0.0, backoff_max=0.0
    )
    try:
        with pytest.raises(ShlinkTransportError):
            await client.get("/short-urls")
    finally:
        await client.aclose()
