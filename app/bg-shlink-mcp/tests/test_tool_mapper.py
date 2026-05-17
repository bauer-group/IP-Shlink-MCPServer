"""
Tests for src/shlink/tool_mapper.py - name overrides + catalogue rendering.

We don't spin up a real FastMCP here; the unit-level behaviour we care
about is the operationId -> friendly-name mapping and the catalogue
rendering, both of which work against simple data structures.
"""

from __future__ import annotations

from shlink import tool_mapper


def _spec_with(paths: dict) -> dict:
    """Minimal OpenAPI dict that satisfies _build_mcp_names_map's walk."""
    return {"openapi": "3.0.0", "info": {"title": "t", "version": "0"}, "paths": paths}


def test_name_override_matches_short_urls_post():
    spec = _spec_with({
        "/short-urls": {"post": {"operationId": "createShortUrl"}},
    })
    mapping = tool_mapper._build_mcp_names_map(spec)
    assert mapping["createShortUrl"] == "create_short_url"


def test_name_override_strips_v3_prefix():
    spec = _spec_with({
        "/rest/v3/short-urls/{shortCode}/visits": {
            "get": {"operationId": "getShortUrlVisits"},
        },
    })
    mapping = tool_mapper._build_mcp_names_map(spec)
    assert mapping["getShortUrlVisits"] == "get_short_url_visits"


def test_name_override_strips_version_template_prefix():
    """Shlink's real spec uses /rest/v{version}/... — the templated form
    must normalise the same way as a concrete /rest/v3/... path."""
    spec = _spec_with({
        "/rest/v{version}/short-urls": {"post": {"operationId": "createShortUrl"}},
    })
    mapping = tool_mapper._build_mcp_names_map(spec)
    assert mapping["createShortUrl"] == "create_short_url"


def test_name_override_omits_unknown_route():
    spec = _spec_with({
        "/something-shlink-might-add-later": {"get": {"operationId": "newOp"}},
    })
    mapping = tool_mapper._build_mcp_names_map(spec)
    assert "newOp" not in mapping


def test_route_map_excludes_mercure():
    pattern_strings = [r.pattern.pattern if hasattr(r.pattern, "pattern") else r.pattern for r in tool_mapper.SHLINK_ROUTE_MAPS]
    assert any("mercure-info" in str(p) for p in pattern_strings)


def test_normalize_spec_strips_version_prefix_and_drops_version_param():
    """Root-cause fix for the double `/rest/v3` bug.

    Shlink ships paths like `/rest/v{version}/short-urls`; our httpx base_url
    already ends in `/rest/v3`. Without normalisation, FastMCP appends a
    SECOND `/rest/v3` and Shlink 404s. After normalisation the path is just
    `/short-urls` and the `version` path parameter is gone from the operation.
    """
    spec = _spec_with({
        "/rest/v{version}/short-urls": {
            "get": {
                "operationId": "listShortUrls",
                "parameters": [
                    {"name": "version", "in": "path", "required": True,
                     "schema": {"type": "string", "enum": ["3", "2", "1"]}},
                    {"name": "page", "in": "query",
                     "schema": {"type": "integer"}},
                ],
            },
        },
    })
    normalised = tool_mapper._normalize_spec_for_v3(spec)

    # Path was rewritten — `/rest/v{version}` prefix is gone.
    assert "/short-urls" in normalised["paths"]
    assert "/rest/v{version}/short-urls" not in normalised["paths"]

    # `version` path-param dropped, query params survive.
    params = normalised["paths"]["/short-urls"]["get"]["parameters"]
    param_names = [p["name"] for p in params]
    assert "version" not in param_names
    assert "page" in param_names


def test_normalize_spec_leaves_non_versioned_paths_untouched():
    spec = _spec_with({
        "/short-urls": {"get": {"operationId": "x", "parameters": []}},
        "/health": {"get": {"operationId": "h"}},
    })
    normalised = tool_mapper._normalize_spec_for_v3(spec)
    assert set(normalised["paths"]) == {"/short-urls", "/health"}


def test_normalize_spec_does_not_mutate_input():
    """SpecCache reuses the input dict; mutation would corrupt cached state."""
    spec = _spec_with({
        "/rest/v{version}/x": {
            "get": {"parameters": [{"name": "version", "in": "path"}]},
        },
    })
    original_paths = list(spec["paths"].keys())
    tool_mapper._normalize_spec_for_v3(spec)
    assert list(spec["paths"].keys()) == original_paths
    assert spec["paths"]["/rest/v{version}/x"]["get"]["parameters"][0]["name"] == "version"


async def test_catalogue_renders_empty_when_no_tools():
    class _StubMcp:
        async def list_tools(self):
            return []

    md = await tool_mapper.render_catalogue_markdown(_StubMcp())  # type: ignore[arg-type]
    assert "Tool count:" in md and "0" in md


async def test_catalogue_renders_with_tools():
    class _Tool:
        def __init__(self, name: str, description: str) -> None:
            self.name = name
            self.description = description

    class _StubMcp:
        async def list_tools(self):
            return [
                _Tool("create_short_url", "Create a new short URL."),
                _Tool("list_short_urls", "List short URLs."),
            ]

    md = await tool_mapper.render_catalogue_markdown(_StubMcp())  # type: ignore[arg-type]
    assert "create_short_url" in md
    assert "list_short_urls" in md
    assert "Tool count:** 2" in md
