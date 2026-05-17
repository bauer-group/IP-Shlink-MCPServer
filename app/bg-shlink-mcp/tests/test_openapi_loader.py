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


@respx.mock
async def test_load_spec_raises_when_no_operations():
    """An empty `paths` must fail loudly.

    Regression test for the silent-failure mode where the server boots with
    zero MCP tools but reports healthy.
    """
    empty_paths_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Empty", "version": "0.0.0"},
        "paths": {},
    }
    respx.get("https://example.test/empty.json").mock(
        return_value=httpx.Response(200, json=empty_paths_spec)
    )
    with pytest.raises(SpecLoadError, match="0 operations"):
        await load_spec("https://example.test/empty.json")


# ── External $ref resolution (runtime bundling) ──────────────────────────────


@respx.mock
async def test_load_spec_resolves_external_refs_from_url():
    """A modular spec served at a URL must have its external `$ref` pointers
    resolved by fetching the referenced files relative to the spec URL.

    This is the canonical Shlink-spec-from-raw.githubusercontent.com case:
    the index doc references `paths/foo.json`, which lives next to it on the
    same server. Without runtime resolution, the loader sees `paths` populated
    but zero operations (silent zero-tool surface).
    """
    index_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Modular", "version": "0.0.1"},
        "paths": {
            "/short-urls": {"$ref": "paths/short-urls.json"},
            "/health": {"$ref": "paths/health.json"},
        },
    }
    short_urls_path = {
        "get": {"operationId": "listShortUrls", "summary": "List short URLs"},
        "post": {"operationId": "createShortUrl", "summary": "Create one"},
    }
    health_path = {
        "get": {"operationId": "health", "summary": "Health"},
    }
    respx.get("https://example.test/spec/swagger.json").mock(
        return_value=httpx.Response(200, json=index_spec)
    )
    respx.get("https://example.test/spec/paths/short-urls.json").mock(
        return_value=httpx.Response(200, json=short_urls_path)
    )
    respx.get("https://example.test/spec/paths/health.json").mock(
        return_value=httpx.Response(200, json=health_path)
    )

    loaded = await load_spec("https://example.test/spec/swagger.json")
    assert loaded.operation_count == 3  # 2 short-urls + 1 health
    # External refs are inlined — no `$ref` keys remain in the paths tree.
    assert "$ref" not in loaded.spec["paths"]["/short-urls"]
    assert loaded.spec["paths"]["/short-urls"]["get"]["operationId"] == "listShortUrls"
    assert loaded.spec["paths"]["/health"]["get"]["operationId"] == "health"


@respx.mock
async def test_load_spec_external_ref_fetch_failure_is_reported():
    """If a referenced file is unreachable, the error must include both the
    source URL and the failing ref so debugging doesn't require guessing."""
    index_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Broken", "version": "0.0.1"},
        "paths": {"/foo": {"$ref": "paths/missing.json"}},
    }
    respx.get("https://example.test/spec/swagger.json").mock(
        return_value=httpx.Response(200, json=index_spec)
    )
    respx.get("https://example.test/spec/paths/missing.json").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(SpecLoadError, match="resolve external"):
        await load_spec("https://example.test/spec/swagger.json")


async def test_load_spec_resolves_external_refs_from_local_file(tmp_path: Path):
    """The same external-ref resolution must work for file:// sources too —
    relative refs are resolved against the spec file's own directory."""
    paths_dir = tmp_path / "paths"
    paths_dir.mkdir()
    (paths_dir / "short-urls.json").write_text(
        json.dumps({
            "get": {"operationId": "listShortUrls", "summary": "List"},
            "post": {"operationId": "createShortUrl", "summary": "Create"},
        }),
        encoding="utf-8",
    )
    index_path = tmp_path / "swagger.json"
    index_path.write_text(
        json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "FileModular", "version": "0.0.1"},
            "paths": {"/short-urls": {"$ref": "paths/short-urls.json"}},
        }),
        encoding="utf-8",
    )

    loaded = await load_spec(f"file://{index_path.as_posix()}")
    assert loaded.operation_count == 2
    assert "$ref" not in loaded.spec["paths"]["/short-urls"]


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
