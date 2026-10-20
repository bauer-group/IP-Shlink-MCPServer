"""Shlink's health probe as an MCP tool, addressed under the versioned API base.

Shlink's OpenAPI spec documents the probe only at the UNVERSIONED alias
``/rest/health`` — the one REST path outside ``/rest/v{version}``. The profile's
``normalize.strip_path_prefix`` therefore left it untouched, and the generated
``health`` tool was joined onto a backend base that already ends in ``/rest/v3``:
every call went to ``/rest/v3/rest/health`` and came back 404.

Shlink registers the same action under the versioned prefix too
(``Rest\\ConfigProvider::applyRoutesPrefix`` adds ``/rest/health`` as an alias of
``/rest/v{1|2|3}/health`` — v4.6.0 through v5.x), so ``/health`` relative to
``api_base_path`` is the canonical address. The profile excludes the generated
route; this module registers the replacement under the same name and spec tag,
so clients and prompts that call ``health`` keep working.

Wired in as a profile tool source (``"source": "python"``), which the assembler
runs after the constructing OpenAPI source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bg_mcpcore.tools.protocol import UpstreamError

if TYPE_CHECKING:
    from bg_mcpcore.tools.protocol import ToolContext
    from fastmcp import FastMCP

# 200 = pass; 503 = Shlink is up but cannot reach its database (status "fail").
_HEALTH_STATUSES = frozenset({200, 503})


async def register(mcp: FastMCP, ctx: ToolContext) -> int:
    """Profile tool source ``python``: register the ``health`` tool."""

    @mcp.tool(
        name="health",
        title="Shlink Health",
        description=(
            "Check whether the Shlink backend is healthy. Returns Shlink's health "
            "document: `status` is `pass`, or `fail` when Shlink cannot reach its "
            "database, plus the running Shlink `version`."
        ),
        tags={"Monitoring"},  # the spec's tag for the operation it replaces
        # Same hints the OpenAPI source gives every GET it generates.
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
    )
    async def health() -> dict[str, Any]:
        response = await ctx.request("GET", "/health")
        try:
            body = response.json()
        except ValueError:
            body = None
        # A 503 carrying Shlink's own health document answers the question asked
        # (the database is down) — it is not a failed call. Anything else, a 404 or
        # a proxy's HTML error page, is.
        if response.status_code in _HEALTH_STATUSES and isinstance(body, dict) and "status" in body:
            return body
        raise UpstreamError(response.status_code, body if isinstance(body, dict) else {})

    return 1


__all__ = ["register"]
