"""Bound the visit-list tools when the model leaves out the page size.

Shlink treats a missing ``itemsPerPage`` on its visit lists as "all items"
(``AbstractInfinitePaginableListParams``: ``null`` or ``< 0`` -> ``ALL_ITEMS``).
The generated tools mark the parameter optional, so a bare
``get_non_orphan_visits()`` pulled the instance's whole visit history into one
tool result — enough to flood the model's context, or to run into the upstream
HTTP timeout on a busy instance.

A tool description can only ask the model to paginate; this middleware makes a
bounded page the default. A call to one of the visit-list tools that carries no
``itemsPerPage`` gets ``DEFAULT_ITEMS_PER_PAGE``. An explicit value — Shlink's
``-1`` for "everything" included — passes through untouched. Every response
carries ``pagination.totalItems``, so a count never needs the rows themselves.

Wired in as a profile tool source (``"source": "python"``), so the middleware is
assembled — and tested — together with the tool surface it governs. It adds no
tools of its own and returns 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware

if TYPE_CHECKING:
    import mcp.types as mt
    from bg_mcpcore.tools.protocol import ToolContext
    from fastmcp import FastMCP
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.base import ToolResult

# Small enough to leave room in the model's context, large enough for a glance at
# recent activity. Keep in sync with the visit-list descriptions in the profile
# and the visit-list resource paths in extensions.json (a test checks both).
DEFAULT_ITEMS_PER_PAGE = 20

# Tools whose upstream endpoint returns every item when itemsPerPage is absent.
VISIT_LIST_TOOLS = frozenset(
    {
        "get_short_url_visits",
        "get_tag_visits",
        "get_domain_visits",
        "get_orphan_visits",
        "get_non_orphan_visits",
    }
)


class DefaultVisitPageSize(Middleware):
    """Fill in ``itemsPerPage`` on visit-list calls that leave it out."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        message = context.message
        arguments = message.arguments or {}
        if message.name in VISIT_LIST_TOOLS and arguments.get("itemsPerPage") is None:
            arguments = {**arguments, "itemsPerPage": DEFAULT_ITEMS_PER_PAGE}
            context = context.copy(message=message.model_copy(update={"arguments": arguments}))
        return await call_next(context)


async def register(mcp: FastMCP, ctx: ToolContext) -> int:
    """Profile tool source ``python``: install the page-size middleware."""
    # A rename in the profile would silently take a tool out of the bound — say so.
    # A warning rather than a refusal to start: a spec pinned to an older Shlink
    # legitimately lacks some of these endpoints.
    missing = VISIT_LIST_TOOLS - {tool.name for tool in await mcp.list_tools()}
    if missing:
        ctx.logger.warning("paging_defaults.tools_missing", tools=sorted(missing))
    mcp.add_middleware(DefaultVisitPageSize())
    return 0


__all__ = ["DEFAULT_ITEMS_PER_PAGE", "VISIT_LIST_TOOLS", "DefaultVisitPageSize", "register"]
