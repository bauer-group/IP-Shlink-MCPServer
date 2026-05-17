"""
Tests for src/shlink/openapi_loader.py - load + refresh cycle.

A real OpenAPI spec is ~150KB. We use a minimal stub spec to keep tests fast
and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from shlink.openapi_loader import SpecCache, SpecLoadError, load_spec


_STUB_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Stub Shlink", "version": "0.0.1"},
    "paths": {
        "/short-urls": {
            "get": {"operationId": "listShortUrls", "summary": "List short URLs"},
            "post": {"operationId": "createShortUrl", "summary": "Create one"},
        },
        "/health": {
            "get": {"operationId": "health", "summary": "Health"},
        },
    },
}


# ── Remote fetch ─────────────────────────────────────────────────────────────


@respx.mock
async def test_load_spec_from_https():
    respx.get("https://example.test/spec.json").mock(
        return_value=httpx.Response(200, json=_STUB_SPEC)
    )
    loaded = await load_spec("https://example.test/spec.json")
    assert loaded.title == "Stub Shlink"
    assert loaded.info_version == "0.0.1"
    assert loaded.operation_count == 3  # GET /short-urls, POST /short-urls, GET /health


@respx.mock
async def test_load_spec_raises_on_http_error():
    respx.get("https://example.test/missing.json").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(Exception):
        await load_spec("https://example.test/missing.json")


@respx.mock
async def test_load_spec_raises_when_paths_missing():
    respx.get("https://example.test/bad.json").mock(
        return_value=httpx.Response(200, json={"openapi": "3.0.0", "info": {}})
    )
    with pytest.raises(SpecLoadError, match="paths"):
        await load_spec("https://example.test/bad.json")


# ── Local file ───────────────────────────────────────────────────────────────


async def test_load_spec_from_local_file(tmp_path: Path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_STUB_SPEC), encoding="utf-8")
    loaded = await load_spec(f"file://{spec_path}")
    assert loaded.title == "Stub Shlink"


# ── Cache + refresh ──────────────────────────────────────────────────────────


@respx.mock
async def test_spec_cache_initial_load_succeeds():
    respx.get("https://example.test/spec.json").mock(
        return_value=httpx.Response(200, json=_STUB_SPEC)
    )
    cache = SpecCache("https://example.test/spec.json")
    loaded = await cache.initial_load()
    assert loaded.info_version == "0.0.1"
    assert cache.current.info_version == "0.0.1"


@respx.mock
async def test_spec_cache_refresh_preserves_previous_on_failure():
    """A failed refresh must NOT clear the cached spec."""
    respx.get("https://example.test/spec.json").mock(
        side_effect=[
            httpx.Response(200, json=_STUB_SPEC),
            httpx.Response(503),
        ]
    )
    cache = SpecCache("https://example.test/spec.json")
    await cache.initial_load()
    ok = await cache.refresh_once()
    assert ok is False
    # Cached spec still in place
    assert cache.current.info_version == "0.0.1"


@respx.mock
async def test_spec_cache_refresh_swaps_on_success():
    new_spec = {**_STUB_SPEC, "info": {"title": "Stub Shlink", "version": "0.0.2"}}
    respx.get("https://example.test/spec.json").mock(
        side_effect=[
            httpx.Response(200, json=_STUB_SPEC),
            httpx.Response(200, json=new_spec),
        ]
    )
    cache = SpecCache("https://example.test/spec.json")
    await cache.initial_load()
    assert cache.current.info_version == "0.0.1"
    ok = await cache.refresh_once()
    assert ok is True
    assert cache.current.info_version == "0.0.2"
