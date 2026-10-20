"""What the generated tools ask of the model, and what they tell it.

The profile shapes Shlink's spec into the tool surface: it excludes routes,
renames operations and overrides descriptions. These tests pin the decisions
that keep that surface safe for an AI caller.

The spec is a stand-in carrying the real operationIds and parameters of the
operations concerned (copied from Shlink v5.1.5's ``docs/swagger``); the
profile is the real one.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP


def _query(name: str, *, required: bool = False) -> dict[str, Any]:
    return {"name": name, "in": "query", "required": required, "schema": {"type": "string"}}


def _path(name: str) -> dict[str, Any]:
    return {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}


_OK = {"200": {"description": "ok"}}
_VERSION = _path("version")
_VISIT_FILTERS = [_query("startDate"), _query("endDate"), _query("page"), _query("itemsPerPage")]


def _op(operation_id: str, *params: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "parameters": [_VERSION, *params],
        "responses": _OK,
        **extra,
    }


SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "shlink-surface", "version": "1.0"},
    "paths": {
        "/rest/v{version}/short-urls/shorten": {
            "get": _op(
                "shortenUrl",
                _query("apiKey", required=True),
                _query("longUrl", required=True),
                _query("format"),
                deprecated=True,
            )
        },
        "/rest/v{version}/short-urls/{shortCode}/visits": {
            "get": _op(
                "getShortUrlVisits",
                _path("shortCode"),
                _query("domain"),
                *_VISIT_FILTERS,
                _query("excludeBots"),
            ),
            "delete": _op("deleteShortUrlVisits", _path("shortCode"), _query("domain")),
        },
        "/rest/v{version}/tags/{tag}/visits": {
            "get": _op(
                "getTagVisits",
                _path("tag"),
                *_VISIT_FILTERS,
                _query("excludeBots"),
                _query("domain"),
            )
        },
        "/rest/v{version}/domains/{domain}/visits": {
            "get": _op("getDomainVisits", _path("domain"), *_VISIT_FILTERS, _query("excludeBots"))
        },
        "/rest/v{version}/visits/orphan": {
            "get": _op(
                "getOrphanVisits",
                *_VISIT_FILTERS,
                _query("excludeBots"),
                _query("type"),
                _query("domain"),
            ),
            "delete": _op("deleteOrphanVisits"),
        },
        "/rest/v{version}/visits/non-orphan": {
            "get": _op(
                "getNonOrphanVisits", *_VISIT_FILTERS, _query("excludeBots"), _query("domain")
            )
        },
        "/rest/v{version}/short-urls/{shortCode}/redirect-rules": {
            "post": _op(
                "setShortUrlRedirectRules",
                _path("shortCode"),
                _query("domain"),
                requestBody={
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "redirectRules": {"type": "array", "items": {"type": "object"}}
                                },
                            }
                        }
                    }
                },
            )
        },
    },
}


@pytest.fixture
async def shlink_mcp(build_shlink_mcp) -> FastMCP:  # type: ignore[no-untyped-def]
    return await build_shlink_mcp(SPEC)


async def _tools(mcp: FastMCP) -> dict[str, Any]:
    return {tool.name: tool for tool in await mcp.list_tools()}


# ── secrets ──────────────────────────────────────────────────────────────────


async def test_no_tool_asks_the_model_for_the_api_key(shlink_mcp: FastMCP) -> None:
    """GET /short-urls/shorten reads the key ONLY from ``?apiKey=`` (Shlink's
    ``routes_with_query_api_key``) and ignores the X-Api-Key header this server
    sends, so its tool made the model supply the shared Shlink key as an
    argument — into the chat transcript and the upstream access logs."""
    asking = {
        name: key
        for name, tool in (await _tools(shlink_mcp)).items()
        for key in (tool.parameters.get("properties") or {})
        if key.lower() == "apikey"
    }
    assert not asking, f"tools that take the Shlink API key as an argument: {asking}"
