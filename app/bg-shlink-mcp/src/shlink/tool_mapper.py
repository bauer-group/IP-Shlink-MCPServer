"""
OpenAPI -> MCP tool mapping.

This module is the only place we touch Shlink's OpenAPI specifics. Adding
a new Shlink endpoint -> tool conversion is a 1-line route-map edit; the
generated tool descriptions, schemas, and parameter validation all come
straight from the spec.

We deliberately use FastMCP.from_openapi() instead of hand-written @tool
functions for one reason: Shlink ships breaking API changes between major
versions (v2 -> v3 dropped four endpoints, renamed three). Hand-maintained
code means a multi-day port every time; OpenAPI ingestion makes it a
spec-bump + test run.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from fastmcp.server.openapi import MCPType, RouteMap

logger = structlog.stdlib.get_logger("bg-shlink-mcp.tool_mapper")


# ── Route filter ────────────────────────────────────────────────────────────
# Order matters - first match wins. Anything not matched stays a Tool (default).

SHLINK_ROUTE_MAPS: list[RouteMap] = [
    # Health probe -> Resource (read-only, no side effects, surfaced as shlink://health)
    RouteMap(pattern=r"^/rest/v\d+/health$", mcp_type=MCPType.RESOURCE),
    RouteMap(pattern=r"^/health$", mcp_type=MCPType.RESOURCE),

    # Mercure / SSE event endpoints aren't useful to an LLM and confuse the schema.
    RouteMap(pattern=r"^/rest/v\d+/mercure-info$", mcp_type=MCPType.EXCLUDE),

    # rules/* and tags/parse-csv are administrative subroutes we don't want exposed.
    RouteMap(pattern=r"^/rest/v\d+/rules", mcp_type=MCPType.EXCLUDE),
]


# ── Friendlier tool names ────────────────────────────────────────────────────
# Shlink's operationIds are PascalCase and verbose ("getShortUrlVisits").
# These overrides map (method, path) -> the snake_case name we want the LLM
# to see. Anything missing falls back to FastMCP's auto-naming.

NAME_OVERRIDES: dict[tuple[str, str], str] = {
    # Short URLs
    ("POST",   "/short-urls"):                       "create_short_url",
    ("GET",    "/short-urls"):                       "list_short_urls",
    ("GET",    "/short-urls/{shortCode}"):           "get_short_url",
    ("PATCH",  "/short-urls/{shortCode}"):           "edit_short_url",
    ("DELETE", "/short-urls/{shortCode}"):           "delete_short_url",

    # Redirect rules (per short URL)
    ("GET",    "/short-urls/{shortCode}/redirect-rules"): "get_redirect_rules",
    ("POST",   "/short-urls/{shortCode}/redirect-rules"): "set_redirect_rules",

    # Visits / analytics
    ("GET",    "/short-urls/{shortCode}/visits"):    "get_short_url_visits",
    ("GET",    "/tags/{tag}/visits"):                "get_tag_visits",
    ("GET",    "/domains/{domain}/visits"):          "get_domain_visits",
    ("GET",    "/visits/non-orphan"):                "get_non_orphan_visits",
    ("GET",    "/visits/orphan"):                    "get_orphan_visits",
    ("GET",    "/visits"):                           "get_visits_summary",

    # Tags
    ("GET",    "/tags"):                             "list_tags",
    ("GET",    "/tags/stats"):                       "get_tag_stats",
    ("PUT",    "/tags"):                             "rename_tag",
    ("DELETE", "/tags"):                             "delete_tags",

    # Domains
    ("GET",    "/domains"):                          "list_domains",
    ("PATCH",  "/domains/redirects"):                "set_domain_redirects",
}


# Description overrides - applied only when Shlink's OpenAPI `summary` is
# uninformative or LLM-confusing. Keep this list short on purpose.
DESCRIPTION_OVERRIDES: dict[str, str] = {
    "create_short_url": (
        "Create a new short URL. Provide longUrl (required) and optionally "
        "customSlug, tags, domain, validSince, validUntil, maxVisits, title."
    ),
    "list_short_urls": (
        "List short URLs, optionally filtered by search term, tags, "
        "or date range. Supports pagination via `page` and `itemsPerPage`."
    ),
    "get_short_url_visits": (
        "Return paginated visit data for a single short URL. Filter by "
        "startDate, endDate, domain, excludeBots, or excludeRobots."
    ),
}


def _normalize_path(path: str) -> str:
    """Strip /rest/v3 prefix when present so overrides match either form."""
    return re.sub(r"^/rest/v\d+", "", path) or "/"


def custom_names(route: Any, _existing: str) -> str | None:
    """
    FastMCP from_openapi() name-mapper callback.

    `route` exposes `method` and `path`. We normalise the path to drop the
    version prefix, then look up the override. Returning None lets FastMCP
    keep its default (operationId-based) name.
    """
    method = (getattr(route, "method", None) or "").upper()
    raw_path = getattr(route, "path", None) or ""
    override = NAME_OVERRIDES.get((method, _normalize_path(raw_path)))
    if override:
        return override
    return None


def build_mcp_from_spec(
    *,
    name: str,
    instructions: str,
    spec: dict[str, Any],
    http_client: httpx.AsyncClient,
    auth: Any | None = None,
    lifespan: Any | None = None,
) -> FastMCP:
    """
    Build a FastMCP server with tools auto-generated from the spec.

    Kept thin on purpose - real wiring (config -> client -> spec -> mcp) lives
    in server.py. This function is unit-testable with a stub spec + httpx client.
    """
    kwargs: dict[str, Any] = {
        "openapi_spec": spec,
        "client": http_client,
        "name": name,
        "instructions": instructions,
        "route_maps": SHLINK_ROUTE_MAPS,
        "mcp_names": custom_names,
        "tags": {"shlink"},
    }
    if auth is not None:
        kwargs["auth"] = auth
    if lifespan is not None:
        kwargs["lifespan"] = lifespan

    mcp = FastMCP.from_openapi(**kwargs)
    _apply_description_overrides(mcp)
    logger.info(
        "tools.generated",
        tool_count=_count_registered_tools(mcp),
        resource_count=_count_registered_resources(mcp),
    )
    return mcp


def _apply_description_overrides(mcp: FastMCP) -> None:
    """Walk the registered tools and apply our description overrides in place."""
    registry = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    if registry is None:
        return
    tools = getattr(registry, "_tools", None) or getattr(registry, "tools", None) or {}
    for tool_name, tool in tools.items():
        if tool_name in DESCRIPTION_OVERRIDES:
            try:
                tool.description = DESCRIPTION_OVERRIDES[tool_name]
            except AttributeError:
                # Newer FastMCP versions may use a frozen model - log and move on.
                logger.debug("tools.description_override_skipped", tool=tool_name)


def _count_registered_tools(mcp: FastMCP) -> int:
    return _count_registry(mcp, ("_tool_manager", "tool_manager"), ("_tools", "tools"))


def _count_registered_resources(mcp: FastMCP) -> int:
    return _count_registry(mcp, ("_resource_manager", "resource_manager"), ("_resources", "resources"))


def _count_registry(mcp: FastMCP, manager_attrs: tuple[str, ...], dict_attrs: tuple[str, ...]) -> int:
    manager = None
    for attr in manager_attrs:
        manager = getattr(mcp, attr, None)
        if manager is not None:
            break
    if manager is None:
        return 0
    for attr in dict_attrs:
        items = getattr(manager, attr, None)
        if isinstance(items, dict):
            return len(items)
    return 0


# ── Catalogue rendering (used by `bg-shlink-mcp tools` and CI doc generator) ─


def render_catalogue_markdown(mcp: FastMCP) -> str:
    """Render a Markdown catalogue of registered tools - powers docs/tools.md."""
    registry = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    tools = (
        getattr(registry, "_tools", None) or getattr(registry, "tools", None) or {}
        if registry
        else {}
    )

    lines: list[str] = [
        "# Shlink MCP - Tool Catalogue",
        "",
        "> Auto-generated from the live Shlink OpenAPI spec. Do not hand-edit.",
        "",
        f"**Tool count:** {len(tools)}",
        "",
    ]
    for tool_name in sorted(tools):
        tool = tools[tool_name]
        description = getattr(tool, "description", "") or ""
        lines.append(f"## `{tool_name}`")
        lines.append("")
        if description:
            lines.append(description.strip())
            lines.append("")
    return "\n".join(lines)
