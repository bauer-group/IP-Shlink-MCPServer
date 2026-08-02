"""Make generated tool input-schemas legal for the Anthropic Messages API.

Anthropic validates every ``tools[*].input_schema.properties`` KEY against
``^[a-zA-Z0-9_.-]{1,64}$`` and rejects the ENTIRE tools array on the first
violation — one bad key bricks the whole session, not just the affected tool.

Shlink's OpenAPI spec names three query parameters with PHP array brackets
(``docs/swagger/paths/``)::

    GET    /short-urls   tags[]   excludeTags[]   -> list_short_urls
    DELETE /tags         tags[]                   -> delete_tags

FastMCP copies the OpenAPI parameter name verbatim into the JSON-Schema
property key, so the brackets land in the tool schema and Anthropic 400s.

Why the brackets cannot simply be dropped from the spec
-------------------------------------------------------
``[]`` is PHP's *serialization* convention, not part of Shlink's contract —
``ShortUrlsParamsInputFilter::TAGS`` is plain ``'tags'``. PHP's query parser
turns ``?tags[]=a&tags[]=b`` into ``$_GET['tags'] = ['a', 'b']``, whereas the
bracket-less ``?tags=a&tags=b`` collapses to the LAST value only. Renaming the
parameter in the spec would therefore trade a loud 400 for two silent bugs:
``list_short_urls`` would filter by one tag instead of all of them, and
``delete_tags`` would hand a string to ``deleteTags(array $tagNames)`` -> 500.

So the schema key and the wire name must diverge, and FastMCP already models
exactly that: ``HTTPRoute.parameter_map`` maps ``{mcp_arg: {location,
openapi_name}}`` and the request director serializes via ``openapi_name``. It
just never populates the two differently. This module does — after the OpenAPI
source has built the tools, it rewrites the illegal schema keys and repoints
the parameter map at the original bracketed wire names.

Wired in as the profile's third tool source (``"source": "python"``), which the
assembler runs AFTER the constructing OpenAPI source, so every generated tool is
already present. It adds no tools of its own and returns 0.

Deliberately generic rather than a two-name allowlist: any future Shlink spec
change (or a swap to a different OpenAPI backend) that introduces a bracket, a
space, a non-ASCII character, or a >64-char parameter produces the same
total-failure mode. Anything this pass cannot repair raises at startup — a
container that refuses to boot is visible in ops; one that boots and bricks
every client is not.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bg_mcpcore.tools.protocol import ToolContext
    from fastmcp import FastMCP

# Anthropic's tool input-schema property-key rule. Keep in sync with the API
# error text: "Property keys should match pattern '^[a-zA-Z0-9_.-]{1,64}$'".
LEGAL_KEY = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")

_ILLEGAL_CHARS = re.compile(r"[^a-zA-Z0-9_.-]")


def sanitize_key(name: str, taken: set[str]) -> str:
    """Derive a schema-legal property key from an OpenAPI parameter name.

    Illegal characters are dropped (``tags[]`` -> ``tags``) rather than replaced,
    because the bracket suffix is pure PHP notation and the bare name is what
    Shlink actually calls the field. A numeric suffix breaks ties if the cleaned
    name is already taken by another parameter on the same operation.
    """
    base = _ILLEGAL_CHARS.sub("", name)[:64] or "param"
    candidate = base
    counter = 2
    while candidate in taken:
        suffix = f"_{counter}"
        candidate = f"{base[: 64 - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def _rename_in_schema(schema: dict[str, Any] | None, old: str, new: str) -> None:
    """Rename one property key in a JSON Schema, preserving property order."""
    if not isinstance(schema, dict):
        return
    properties = schema.get("properties")
    if isinstance(properties, dict) and old in properties:
        schema["properties"] = {(new if k == old else k): v for k, v in properties.items()}
    required = schema.get("required")
    if isinstance(required, list) and old in required:
        schema["required"] = [new if item == old else item for item in required]


def _illegal_keys(tool: Any) -> list[str]:
    properties = (getattr(tool, "parameters", None) or {}).get("properties") or {}
    return [key for key in properties if not LEGAL_KEY.match(key)]


def repair_tool(tool: Any) -> dict[str, str]:
    """Rewrite a tool's illegal schema keys; return ``{new: original}``.

    Raises if the tool carries an illegal key but no ``parameter_map`` to record
    the original wire name in — silently renaming the key there would change the
    outbound request rather than just its schema.
    """
    illegal = _illegal_keys(tool)
    if not illegal:
        return {}

    route = getattr(tool, "_route", None)
    parameter_map = getattr(route, "parameter_map", None)
    if not isinstance(parameter_map, dict):
        raise RuntimeError(
            f"Tool {tool.name!r} has schema-illegal property keys {illegal} but no OpenAPI "
            "parameter map to preserve the original wire names in — refusing to rename them."
        )

    renamed: dict[str, str] = {}
    for old in illegal:
        taken = set(tool.parameters.get("properties") or {}) | set(parameter_map)
        new = sanitize_key(old, taken)
        _rename_in_schema(tool.parameters, old, new)
        _rename_in_schema(getattr(route, "flat_param_schema", None), old, new)
        mapping = parameter_map.pop(old, {"location": "query", "openapi_name": old})
        # openapi_name is what the request director puts on the wire — it must
        # keep the brackets even though the MCP-facing key has lost them.
        parameter_map[new] = {**mapping, "openapi_name": mapping.get("openapi_name", old)}
        renamed[new] = old
    return renamed


async def register(mcp: FastMCP, ctx: ToolContext) -> int:
    """Profile tool source ``python``: repair, then verify, every tool schema."""
    tools = await mcp.list_tools()

    repaired: dict[str, dict[str, str]] = {}
    for tool in tools:
        renamed = repair_tool(tool)
        if renamed:
            repaired[tool.name] = renamed

    # Verify against the tools the server will actually serve, not the objects we
    # just touched — this is the assertion that keeps a future FastMCP refactor
    # (copied tools, lazily rebuilt schemas) from re-introducing the 400.
    remaining = {
        tool.name: keys for tool in await mcp.list_tools() if (keys := _illegal_keys(tool))
    }
    if remaining:
        raise RuntimeError(
            f"Tool schemas still carry property keys that violate {LEGAL_KEY.pattern}: "
            f"{remaining}. The Anthropic Messages API rejects the entire tools array on "
            "these, so refusing to start."
        )

    if repaired:
        ctx.logger.info("tools.schema_keys_sanitized", tools=repaired)
    return 0


__all__ = ["LEGAL_KEY", "register", "repair_tool", "sanitize_key"]
