"""Regression tests for the Anthropic tool-schema key repair (issue #7).

Anthropic rejects the whole tools array when any input-schema property key
violates ``^[a-zA-Z0-9_.-]{1,64}$``, and Shlink's spec ships ``tags[]`` /
``excludeTags[]``. The two properties that matter are checked end to end:

* the MCP-facing schema key loses the brackets, and
* the outbound query string keeps them — otherwise PHP collapses repeated
  ``?tags=a&tags=b`` to the last value and the filter silently narrows.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from bg_mcpcore.tools.protocol import ToolContext
from fastmcp import FastMCP

from tool_schema_compat import LEGAL_KEY, register, repair_tool, sanitize_key

# A minimal stand-in for Shlink's GET /short-urls + DELETE /tags — same bracketed
# array parameters, none of the 240 KB of noise.
SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "bracket-spec", "version": "1.0"},
    "paths": {
        "/short-urls": {
            "get": {
                "operationId": "listShortUrls",
                "parameters": [
                    {
                        "name": "searchTerm",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "tags[]",
                        "in": "query",
                        "schema": {"type": "array", "items": {"type": "string"}},
                    },
                    {
                        "name": "excludeTags[]",
                        "in": "query",
                        "schema": {"type": "array", "items": {"type": "string"}},
                    },
                ],
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


async def _build_server() -> FastMCP:
    client = httpx.AsyncClient(base_url="https://shlink.test/rest/v3")
    return FastMCP.from_openapi(openapi_spec=SPEC, client=client, name="test")


async def _tool(mcp: FastMCP, name: str) -> Any:
    return next(tool for tool in await mcp.list_tools() if tool.name == name)


# ── the bug, reproduced ──────────────────────────────────────────────────────


async def test_generated_schema_starts_out_illegal() -> None:
    """Guard the premise: without the repair, FastMCP emits bracketed keys."""
    tool = await _tool(await _build_server(), "listShortUrls")
    illegal = [k for k in tool.parameters["properties"] if not LEGAL_KEY.match(k)]
    assert illegal == ["tags[]", "excludeTags[]"]


# ── the repair ───────────────────────────────────────────────────────────────


async def test_register_makes_every_key_legal() -> None:
    mcp = await _build_server()
    assert await register(mcp, ToolContext(settings=None)) == 0

    for tool in await mcp.list_tools():
        for key in tool.parameters.get("properties") or {}:
            assert LEGAL_KEY.match(key), f"{tool.name}.{key} still illegal"


async def test_repair_keeps_property_schemas_and_names() -> None:
    mcp = await _build_server()
    await register(mcp, ToolContext(settings=None))
    properties = (await _tool(mcp, "listShortUrls")).parameters["properties"]

    assert set(properties) == {"searchTerm", "tags", "excludeTags"}
    assert properties["tags"]["type"] == "array"
    assert properties["excludeTags"]["items"] == {"type": "string"}


async def test_wire_name_keeps_the_brackets() -> None:
    """The whole point: the schema key is repaired, the query string is not."""
    mcp = await _build_server()
    await register(mcp, ToolContext(settings=None))
    tool = await _tool(mcp, "listShortUrls")

    assert tool._route.parameter_map["tags"]["openapi_name"] == "tags[]"
    assert tool._route.parameter_map["excludeTags"]["openapi_name"] == "excludeTags[]"

    request = tool._director.build(
        tool._route, {"tags": ["alpha", "beta"], "searchTerm": "x"}, "https://shlink.test/rest/v3"
    )
    # httpx percent-encodes the brackets; PHP decodes %5B%5D back to [] and builds
    # $_GET['tags'] = ['alpha', 'beta'].
    query = str(request.url.query, "utf-8")
    assert "tags%5B%5D=alpha" in query
    assert "tags%5B%5D=beta" in query
    assert "tags=alpha" not in query


async def test_repair_is_idempotent() -> None:
    mcp = await _build_server()
    await register(mcp, ToolContext(settings=None))
    await register(mcp, ToolContext(settings=None))

    tool = await _tool(mcp, "listShortUrls")
    assert set(tool.parameters["properties"]) == {"searchTerm", "tags", "excludeTags"}
    assert tool._route.parameter_map["tags"]["openapi_name"] == "tags[]"


# ── fail-fast guards ─────────────────────────────────────────────────────────


async def test_illegal_key_without_a_parameter_map_raises() -> None:
    """A hand-written tool cannot be silently renamed — its wire name is its name."""

    class _Bare:
        def __init__(self) -> None:
            self.name = "handwritten"
            self.parameters = {"type": "object", "properties": {"weird key": {"type": "string"}}}

    with pytest.raises(RuntimeError, match="refusing to rename"):
        repair_tool(_Bare())


async def test_unrepairable_schema_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the repair does not stick, startup fails instead of bricking clients."""
    mcp = await _build_server()
    monkeypatch.setattr("tool_schema_compat.repair_tool", lambda _tool: {})

    with pytest.raises(RuntimeError, match="refusing to start"):
        await register(mcp, ToolContext(settings=None))


# ── key sanitisation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tags[]", "tags"),
        ("excludeTags[]", "excludeTags"),
        ("filter[user][id]", "filteruserid"),
        ("with space", "withspace"),
        ("ok_name.v2-x", "ok_name.v2-x"),
        ("[]", "param"),
        ("x" * 80, "x" * 64),
    ],
)
def test_sanitize_key(raw: str, expected: str) -> None:
    result = sanitize_key(raw, set())
    assert result == expected
    assert LEGAL_KEY.match(result)


def test_sanitize_key_breaks_collisions() -> None:
    assert sanitize_key("tags[]", {"tags"}) == "tags_2"
    assert sanitize_key("tags[]", {"tags", "tags_2"}) == "tags_3"


def test_sanitize_key_collision_suffix_stays_within_64_chars() -> None:
    long_name = "y" * 70
    result = sanitize_key(long_name, {"y" * 64})
    assert len(result) <= 64
    assert LEGAL_KEY.match(result)
