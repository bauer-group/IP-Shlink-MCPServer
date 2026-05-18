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
from fastmcp.server.providers.openapi import MCPType, RouteMap
from mcp.types import ToolAnnotations

logger = structlog.stdlib.get_logger("bg-shlink-mcp.tool_mapper")


# ── Approval gate: HTTP method → MCP tool annotations ──────────────────────
# Shlink's API key has no read-only mode (its role system gates SCOPE, not
# OPERATION — see scripts/generate-shlink-key.py for the upstream context).
# Read/write separation is enforced here instead: every generated tool gets
# annotations that tell MCP clients (Claude Desktop, Inspector, ...) which
# calls are safe to auto-run and which need human approval.
#
# Per MCP spec, these are HINTS — clients are free to ignore them, so this
# is defense-in-depth, not authorization. Keep the Shlink key on a need-to-
# know basis regardless.
#
# Policy: GET is safe-to-auto, everything else needs approval. POST counts
# as destructive because it creates state that costs effort to clean up
# (deleted-and-recreated short URLs lose their visit history).
METHOD_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "GET":    ToolAnnotations(readOnlyHint=True,  destructiveHint=False, openWorldHint=True),
    "POST":   ToolAnnotations(readOnlyHint=False, destructiveHint=True,  idempotentHint=False, openWorldHint=True),
    "PUT":    ToolAnnotations(readOnlyHint=False, destructiveHint=True,  idempotentHint=True,  openWorldHint=True),
    "PATCH":  ToolAnnotations(readOnlyHint=False, destructiveHint=True,  idempotentHint=True,  openWorldHint=True),
    "DELETE": ToolAnnotations(readOnlyHint=False, destructiveHint=True,  idempotentHint=True,  openWorldHint=True),
}


# ── Route filter ────────────────────────────────────────────────────────────
# Order matters - first match wins. Anything not matched stays a Tool (default).

SHLINK_ROUTE_MAPS: list[RouteMap] = [
    # Patterns match the POST-normalisation form (see `_normalize_spec_for_v3`):
    # `/rest/v{version}` has already been stripped from spec paths by the time
    # FastMCP sees them, so every pattern below is version-prefix-free.

    # Health probe -> Resource (read-only, no side effects, surfaced as shlink://health).
    RouteMap(pattern=r"^/health$", mcp_type=MCPType.RESOURCE),

    # Mercure / SSE event endpoints aren't useful to an LLM and confuse the schema.
    RouteMap(pattern=r"^/mercure-info$", mcp_type=MCPType.EXCLUDE),

    # Administrative subroutes we don't want exposed (top-level /rules, not
    # /short-urls/{shortCode}/redirect-rules which we DO want — name overrides
    # surface those as get_redirect_rules / set_redirect_rules).
    RouteMap(pattern=r"^/rules", mcp_type=MCPType.EXCLUDE),
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


_VERSION_PREFIX_RE = re.compile(r"^/rest/v(?:\d+|\{[^}]+\})")


def _normalize_path(path: str) -> str:
    """Strip the version prefix so (method, path) overrides match either form.

    Handles both concrete (`/rest/v3/foo`) and templated (`/rest/v{version}/foo`)
    forms — Shlink ships the spec with the templated form, but a hand-pinned
    spec or test stub might use a concrete version number.
    """
    return _VERSION_PREFIX_RE.sub("", path) or "/"


def _normalize_spec_for_v3(spec: dict[str, Any]) -> dict[str, Any]:
    """Strip `/rest/v{version}` from paths and drop the `version` path param.

    Shlink's spec models the API major as a templated path segment
    (`/rest/v{version}/short-urls`, `version` required, `enum: ['3','2','1']`).
    Two problems if we leave it untouched:
      1. `ShlinkClient` already bakes `/rest/v3` into the httpx base_url, so
         FastMCP appends a SECOND copy — every call lands on `/rest/v3/rest/v3/…`
         and Shlink 404s.
      2. `version` would surface as a required argument on every generated tool,
         forcing the LLM to learn an API-versioning quirk that is purely an
         operator-side decision.

    Pinning to v3 here is consistent with `Settings.shlink_api_base` and matches
    what the test stubs in `tests/test_tool_mapper.py` already assume.
    """
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return spec

    new_paths: dict[str, Any] = {}
    for raw_path, path_item in paths.items():
        new_key = _VERSION_PREFIX_RE.sub("", raw_path) or "/"
        if isinstance(path_item, dict):
            new_item: dict[str, Any] = {}
            for method, operation in path_item.items():
                if isinstance(operation, dict) and isinstance(operation.get("parameters"), list):
                    operation = {
                        **operation,
                        "parameters": [
                            p for p in operation["parameters"]
                            if not (
                                isinstance(p, dict)
                                and p.get("in") == "path"
                                and p.get("name") == "version"
                            )
                        ],
                    }
                new_item[method] = operation
            new_paths[new_key] = new_item
        else:
            new_paths[new_key] = path_item

    return {**spec, "paths": new_paths}


def _build_mcp_names_map(spec: dict[str, Any]) -> dict[str, str]:
    """Translate (method, path) overrides into the {operationId: name} form
    FastMCP 3.x expects.

    FastMCP 2.x took a callback `(route, existing) -> str | None`; 3.x takes
    a plain dict keyed by operationId. We keep maintaining NAME_OVERRIDES in
    the (method, path) form because that's what humans read in the Shlink
    docs — this function bridges to FastMCP's preferred shape by walking the
    spec once at startup.
    """
    overrides: dict[str, str] = {}
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return overrides
    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        normalized = _normalize_path(raw_path)
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if not isinstance(op_id, str) or not op_id:
                continue
            mapped = NAME_OVERRIDES.get((method.upper(), normalized))
            if mapped:
                overrides[op_id] = mapped
    return overrides


class _BuildCounters:
    """Mutable counters populated by the component callback during from_openapi().

    FastMCP 3.x doesn't expose a sync way to count tools/resources after the
    fact (the OpenAPIProvider stores nothing in `_components` for OpenAPI-
    generated entries — it generates them on demand). The component callback
    is the only point where we see every component eagerly, so we count there.
    """

    __slots__ = ("tools", "resources", "annotated", "description_overrides_applied")

    def __init__(self) -> None:
        self.tools = 0
        self.resources = 0
        self.annotated = 0
        self.description_overrides_applied = 0


def _make_component_fn(counters: _BuildCounters):
    """Build a callback for OpenAPIProvider.mcp_component_fn.

    Called once per generated component (Tool / Resource / ResourceTemplate)
    during from_openapi(). We use it to:
      1. Apply method-based MCP annotations (readOnly/destructive hints —
         a real safety signal that MCP clients use to gate auto-run).
      2. Apply our DESCRIPTION_OVERRIDES on top of Shlink's summaries.
      3. Count what was generated so the bootstrap log is accurate.

    Mutates the component in-place. Failures are logged at DEBUG but never
    abort the build — a missing annotation just means the client falls back
    to the safe default (treat as destructive).
    """

    def fn(route: Any, component: Any) -> None:
        type_name = type(component).__name__

        # Components are Tool / OpenAPITool / Resource / OpenAPIResource / Template etc.
        # The "Tool" substring is the most reliable cross-version discriminator.
        is_tool = "Tool" in type_name

        if is_tool:
            counters.tools += 1
        else:
            counters.resources += 1

        component_name = getattr(component, "name", None)
        if isinstance(component_name, str) and component_name in DESCRIPTION_OVERRIDES:
            try:
                component.description = DESCRIPTION_OVERRIDES[component_name]
                counters.description_overrides_applied += 1
            except (AttributeError, ValueError) as exc:
                logger.debug(
                    "tools.description_override_skipped",
                    component=component_name,
                    reason=str(exc),
                )

        if is_tool:
            method = (getattr(route, "method", None) or "").upper()
            annotations = METHOD_ANNOTATIONS.get(method)
            if annotations is None:
                logger.debug(
                    "tools.annotation_skipped",
                    component=component_name,
                    reason="unknown_method",
                    method=method,
                )
                return
            try:
                component.annotations = annotations
                counters.annotated += 1
            except (AttributeError, ValueError) as exc:
                logger.debug(
                    "tools.annotation_skipped",
                    component=component_name,
                    reason="frozen_model",
                    error=str(exc),
                )

    return fn


def build_mcp_from_spec(
    *,
    name: str,
    instructions: str,
    spec: dict[str, Any],
    http_client: httpx.AsyncClient,
    auth: Any | None = None,
    lifespan: Any | None = None,
    icon_url: str | None = None,
    website_url: str | None = None,
) -> FastMCP:
    """
    Build a FastMCP server with tools auto-generated from the spec.

    Kept thin on purpose - real wiring (config -> client -> spec -> mcp) lives
    in server.py. This function is unit-testable with a stub spec + httpx client.

    `icon_url` and `website_url` are surfaced on the OAuth consent screen
    rendered by FastMCP's OAuthProxy. Both are optional - omitting them falls
    back to FastMCP's defaults (no icon, plain-text server name).
    """
    from mcp.types import Icon

    counters = _BuildCounters()

    spec = _normalize_spec_for_v3(spec)

    kwargs: dict[str, Any] = {
        "openapi_spec": spec,
        "client": http_client,
        "name": name,
        "instructions": instructions,
        "route_maps": SHLINK_ROUTE_MAPS,
        "mcp_names": _build_mcp_names_map(spec),
        "mcp_component_fn": _make_component_fn(counters),
        "tags": {"shlink"},
    }
    if auth is not None:
        kwargs["auth"] = auth
    if lifespan is not None:
        kwargs["lifespan"] = lifespan
    if icon_url:
        # FastMCP picks icons[0] for the consent screen.
        kwargs["icons"] = [Icon(src=icon_url, mimeType="image/svg+xml")]
    if website_url:
        kwargs["website_url"] = website_url

    mcp = FastMCP.from_openapi(**kwargs)
    logger.info(
        "tools.generated",
        tool_count=counters.tools,
        resource_count=counters.resources,
        tools_annotated=counters.annotated,
        description_overrides_applied=counters.description_overrides_applied,
    )
    return mcp


# ── Catalogue rendering (used by `bg-shlink-mcp tools` and CI doc generator) ─


async def render_catalogue_markdown(mcp: FastMCP) -> str:
    """Render a Markdown catalogue of registered tools — powers docs/tools.md.

    Async because FastMCP 3.x's `list_tools()` is async (it aggregates across
    all registered providers, some of which may resolve tools lazily).
    """
    tools = await mcp.list_tools()
    tools_by_name = {getattr(t, "name", str(i)): t for i, t in enumerate(tools)}

    lines: list[str] = [
        "# Shlink MCP - Tool Catalogue",
        "",
        "> Auto-generated from the live Shlink OpenAPI spec. Do not hand-edit.",
        "",
        f"**Tool count:** {len(tools_by_name)}",
        "",
    ]
    for tool_name in sorted(tools_by_name):
        tool = tools_by_name[tool_name]
        description = getattr(tool, "description", "") or ""
        lines.append(f"## `{tool_name}`")
        lines.append("")
        if description:
            lines.append(description.strip())
            lines.append("")
    return "\n".join(lines)
