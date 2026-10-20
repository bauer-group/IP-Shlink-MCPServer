"""Every tool must address a path Shlink serves under ``api_base_path``.

The profile's backend base already ends in ``/rest/v3`` and the OpenAPI source
strips ``/rest/v{version}`` from the spec, so a generated tool's path is joined
onto that base. Shlink's spec, however, documents three paths OUTSIDE the
versioned prefix, which the strip leaves untouched::

    GET /rest/health          health          -> /rest/v3/rest/health      (404)
    GET /{shortCode}          shortUrl        -> /rest/v3/{shortCode}      (404)
    GET /{shortCode}/track    trackShortUrl   -> /rest/v3/{shortCode}/track (404)

All three tools failed on every call. ``health`` is a real REST endpoint that
Shlink also serves under the versioned prefix, so it is re-registered at
``/health``; the other two are the public redirect and tracking-pixel routes —
not API operations at all (calling them records a visit) — and are excluded.

The spec here is a stand-in with one route from each family; the profile, the
route maps and the python tool sources are the real ones.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

SHLINK_URL = "http://shlink:8080"  # what the base_none_env fixture sets
HEALTH_URL = f"{SHLINK_URL}/rest/v3/health"

_OK = {"200": {"description": "ok"}}
_VERSION = {"name": "version", "in": "path", "required": True, "schema": {"type": "string"}}
_SHORT_CODE = {"name": "shortCode", "in": "path", "required": True, "schema": {"type": "string"}}

SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "shlink-path-families", "version": "1.0"},
    "paths": {
        "/rest/v{version}/short-urls": {
            "get": {"operationId": "listShortUrls", "parameters": [_VERSION], "responses": _OK}
        },
        "/rest/health": {"get": {"operationId": "health", "responses": _OK}},
        "/{shortCode}": {
            "get": {"operationId": "shortUrl", "parameters": [_SHORT_CODE], "responses": _OK}
        },
        "/{shortCode}/track": {
            "get": {"operationId": "trackShortUrl", "parameters": [_SHORT_CODE], "responses": _OK}
        },
    },
}

PASS_BODY = {"status": "pass", "version": "5.1.7", "links": {"about": "https://shlink.io"}}


@pytest.fixture
async def shlink_mcp(build_shlink_mcp) -> FastMCP:  # type: ignore[no-untyped-def]
    """The server assembled the way main.py does, from the stand-in spec."""
    return await build_shlink_mcp(SPEC)


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The upstream client retries a 503 with exponential backoff — skip the sleeps."""

    async def _no_sleep(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("bg_mcpcore.http.client.sleep_backoff", _no_sleep)


# ── the invariant ────────────────────────────────────────────────────────────


async def test_no_generated_tool_escapes_the_api_base(shlink_mcp: FastMCP) -> None:
    """A path still carrying ``/rest/`` or a bare top-level parameter was not
    relative to ``/rest/v{version}`` — joined onto the base, it cannot resolve."""
    routes = {
        tool.name: tool._route.path
        for tool in await shlink_mcp.list_tools()
        if getattr(tool, "_route", None) is not None
    }
    escaping = {name: path for name, path in routes.items() if path.startswith(("/rest/", "/{"))}
    assert not escaping, f"tools addressing paths outside the API base: {escaping}"
    assert routes == {"list_short_urls": "/short-urls"}


async def test_public_redirect_routes_are_not_tools(shlink_mcp: FastMCP) -> None:
    names = {tool.name for tool in await shlink_mcp.list_tools()}
    assert not names & {"shortUrl", "trackShortUrl"}


# ── the health tool ──────────────────────────────────────────────────────────


async def test_health_probes_the_versioned_endpoint(shlink_mcp: FastMCP) -> None:
    with respx.mock(assert_all_called=True) as upstream:
        route = upstream.get(HEALTH_URL).respond(200, json=PASS_BODY)
        async with Client(shlink_mcp) as client:
            result = await client.call_tool("health", {})

    assert result.structured_content == PASS_BODY
    assert route.calls.last.request.headers["X-Api-Key"] == "test-shlink-api-key-deadbeef"


async def test_health_reports_a_failing_backend_instead_of_raising(
    shlink_mcp: FastMCP, no_backoff: None
) -> None:
    """Shlink answers 503 + ``status: fail`` when its database is unreachable —
    that is the answer to the question asked, not a tool failure."""
    fail_body = {"status": "fail", "version": "5.1.7", "links": {}}
    with respx.mock() as upstream:
        upstream.get(HEALTH_URL).respond(503, json=fail_body)
        async with Client(shlink_mcp) as client:
            result = await client.call_tool("health", {})

    assert result.structured_content == fail_body


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"title": "Not Found", "status": 404}),
        httpx.Response(503, text="<html>upstream unavailable</html>"),
    ],
    ids=["not-found", "proxy-error-page"],
)
async def test_health_raises_on_anything_but_a_health_document(
    shlink_mcp: FastMCP, no_backoff: None, response: httpx.Response
) -> None:
    with respx.mock() as upstream:
        upstream.get(HEALTH_URL).mock(return_value=response)
        async with Client(shlink_mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("health", {})
