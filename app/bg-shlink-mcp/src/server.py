"""bg-shlink-mcp — the Shlink-specific seam for the bg-mcpcore profile.

The Shlink server is almost entirely declarative (``profiles/shlink.json``): the
25 OpenAPI tools, the static ``X-Api-Key`` outbound auth, all five inbound auth
modes, and the prompt + resource catalogue are pure config. The one piece that
needs real procedural logic is the bulk export — it pages ``/short-urls`` and
renders CSV or JSON — so it lives here as the profile's ``python`` tool source
(``tools: [..., {"source": "python", "register": "server:register_export_task"}]``)
and runs as an MCP task.

Why a python source and not an extension: bg-mcpcore's extensions subsystem
covers prompts + resources (pure transformations of config), but a task has
procedural logic (pagination, format conversion). The config still controls the
declarative surface; only the *how it runs* is code.

Adding another bulk export (tags, domains) = add a ``_fetch_all_*`` + register a
second task; no change to any other code path.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from bg_mcpcore.observability import get_logger

if TYPE_CHECKING:
    from bg_mcpcore.tools.protocol import ToolContext
    from fastmcp import FastMCP

logger = get_logger("bg-shlink-mcp.export")

# Mirrors the former extensions.json `export_short_urls` task block verbatim.
_EXPORT_FORMATS: tuple[str, ...] = ("csv", "json")
_EXPORT_PAGE_SIZE = 200
_EXPORT_POLL_INTERVAL_SECONDS = 2.0


def _cell(value: Any) -> str:
    """Render one CSV cell value — flatten lists/dicts via JSON, None -> ''."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _to_csv(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    # Column set = the union of keys across rows, sorted for determinism. Nested
    # objects (tags, meta) are JSON-encoded into the cell so the CSV stays flat —
    # round-tripping is the caller's problem.
    columns = sorted({k for item in items for k in item})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        writer.writerow({c: _cell(item.get(c)) for c in columns})
    return buffer.getvalue()


def _to_json(items: list[dict[str, Any]]) -> str:
    return json.dumps(items, ensure_ascii=False, indent=2)


async def _fetch_all_short_urls(ctx: ToolContext) -> list[dict[str, Any]]:
    """Page through /short-urls and return the flattened list of objects."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        response = await ctx.request(
            "GET",
            "/short-urls",
            params={"page": page, "itemsPerPage": _EXPORT_PAGE_SIZE},
        )
        response.raise_for_status()
        body = response.json()
        short_urls = body.get("shortUrls", {})
        data = short_urls.get("data", [])
        items.extend(data)
        pagination = short_urls.get("pagination", {})
        current = int(pagination.get("currentPage", page))
        total = int(pagination.get("pagesCount", current))
        if current >= total or not data:
            break
        page += 1
    return items


def register_export_task(mcp: FastMCP, ctx: ToolContext) -> int:
    """Register the ``export_short_urls`` MCP task (profile python tool source).

    Returns the number of tools registered (1) so the assembler's tally is
    accurate. ``ctx`` (capability-scoped to the python source) carries the
    authenticated upstream client; the closure routes every page through
    ``ctx.request`` so the static X-Api-Key is applied exactly as for the
    OpenAPI tools.
    """
    from fastmcp.utilities.tasks import TaskConfig

    task_config = TaskConfig(
        mode="required",
        poll_interval=timedelta(seconds=_EXPORT_POLL_INTERVAL_SECONDS),
    )
    default_format = _EXPORT_FORMATS[0]

    async def export_short_urls(format: str = default_format) -> dict[str, Any]:
        if format not in _EXPORT_FORMATS:
            raise ValueError(
                f"unknown format {format!r}; allowed: {list(_EXPORT_FORMATS)}"
            )
        items = await _fetch_all_short_urls(ctx)
        if format == "csv":
            body, mime = _to_csv(items), "text/csv"
        else:
            body, mime = _to_json(items), "application/json"
        logger.info("export.short_urls_completed", format=format, item_count=len(items))
        return {
            "format": format,
            "mime_type": mime,
            "item_count": len(items),
            "content": body,
        }

    mcp.tool(
        name="export_short_urls",
        title="Export Short URLs",
        description=(
            "Bulk-export every short URL on this Shlink instance as CSV or JSON. "
            "Runs as an MCP task — the AI receives a task_id and polls until "
            "completion. Pages through the entire inventory; large instances may "
            "take a few seconds."
        ),
        tags={"export", "bulk"},
        task=task_config,
    )(export_short_urls)
    return 1


__all__ = ["register_export_task"]
