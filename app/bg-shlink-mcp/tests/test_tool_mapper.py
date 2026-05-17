"""
Tests for src/shlink/tool_mapper.py - name overrides + catalogue rendering.

We don't spin up a real FastMCP here; the unit-level behaviour we care
about is the name-mapper callback and the catalogue rendering, both of
which work against simple data structures.
"""

from __future__ import annotations

from types import SimpleNamespace

from shlink import tool_mapper


def test_name_override_matches_short_urls_post():
    route = SimpleNamespace(method="POST", path="/short-urls")
    assert tool_mapper.custom_names(route, "createShortUrl") == "create_short_url"


def test_name_override_strips_v3_prefix():
    route = SimpleNamespace(method="GET", path="/rest/v3/short-urls/{shortCode}/visits")
    assert tool_mapper.custom_names(route, "getShortUrlVisits") == "get_short_url_visits"


def test_name_override_returns_none_for_unknown_route():
    route = SimpleNamespace(method="GET", path="/something-shlink-might-add-later")
    assert tool_mapper.custom_names(route, "fallback") is None


def test_route_map_excludes_mercure():
    pattern_strings = [r.pattern.pattern if hasattr(r.pattern, "pattern") else r.pattern for r in tool_mapper.SHLINK_ROUTE_MAPS]
    assert any("mercure-info" in str(p) for p in pattern_strings)


def test_catalogue_renders_empty_when_no_tools():
    class _StubManager:
        _tools: dict[str, object] = {}

    class _StubMcp:
        _tool_manager = _StubManager()

    md = tool_mapper.render_catalogue_markdown(_StubMcp())  # type: ignore[arg-type]
    assert "Tool count:" in md and "0" in md


def test_catalogue_renders_with_tools():
    class _Tool:
        def __init__(self, description: str) -> None:
            self.description = description

    class _StubManager:
        _tools = {
            "create_short_url": _Tool("Create a new short URL."),
            "list_short_urls": _Tool("List short URLs."),
        }

    class _StubMcp:
        _tool_manager = _StubManager()

    md = tool_mapper.render_catalogue_markdown(_StubMcp())  # type: ignore[arg-type]
    assert "create_short_url" in md
    assert "list_short_urls" in md
    assert "Tool count:** 2" in md
