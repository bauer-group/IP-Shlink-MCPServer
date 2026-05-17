"""
Shlink MCP Server - CLI entrypoint.

Subcommands:
  serve      Run the MCP server (default when no command is given)
  tools      Print the auto-generated tool catalogue (for docs/tools.md)
  health     Probe Shlink + IdP discovery and exit 0/1

Designed to be the container ENTRYPOINT. `tini --` handles signal forwarding;
typer handles arg parsing and help screens.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

# Allow `python src/main.py` to resolve the package even without an install.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import typer  # noqa: E402
import structlog  # noqa: E402

from config import Settings, get_settings  # noqa: E402
from logging_setup import setup_logging  # noqa: E402

app = typer.Typer(
    name="bg-shlink-mcp",
    help="Remote MCP server for self-hosted Shlink.",
    no_args_is_help=False,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Default to `serve` when called with no subcommand."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


# ── serve ─────────────────────────────────────────────────────────────────────


@app.command()
def serve(
    host: Optional[str] = typer.Option(
        None,
        "--host",
        help="Bind address (overrides MCP_HOST)",
    ),
    port: Optional[int] = typer.Option(
        None,
        "--port",
        help="Listen port (overrides MCP_PORT)",
    ),
    transport: Optional[str] = typer.Option(
        None,
        "--transport",
        help="MCP transport: streamable-http (default) or stdio",
    ),
) -> None:
    """Run the MCP server (default mode)."""
    from server import build_app

    settings = get_settings()
    if host:
        settings.mcp_host = host
    if port:
        settings.mcp_port = port
    chosen_transport = transport or settings.mcp_transport

    asyncio.run(_serve_async(settings, chosen_transport))


async def _serve_async(settings: Settings, transport: str) -> None:
    """Build the app and hand off to FastMCP's transport runner."""
    from server import build_app

    mcp = await build_app(settings)
    logger = structlog.stdlib.get_logger("bg-shlink-mcp.main")
    logger.info(
        "server.starting",
        transport=transport,
        host=settings.mcp_host,
        port=settings.mcp_port,
    )

    if transport == "stdio":
        await mcp.run_stdio_async()
    elif transport == "streamable-http":
        await mcp.run_http_async(
            host=settings.mcp_host,
            port=settings.mcp_port,
            transport="streamable-http",
        )
    else:
        raise typer.BadParameter(f"Unsupported transport: {transport!r}")


# ── tools ─────────────────────────────────────────────────────────────────────


@app.command(name="tools")
def list_tools(
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the catalogue to a file (default: stdout)",
    ),
) -> None:
    """Render the auto-generated tool catalogue from the live Shlink OpenAPI spec."""
    from server import build_app
    from shlink.tool_mapper import render_catalogue_markdown

    async def _render() -> str:
        settings = get_settings()
        mcp = await build_app(settings)
        return render_catalogue_markdown(mcp)

    markdown = asyncio.run(_render())
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8", newline="\n")
        typer.echo(f"wrote: {output}")
    else:
        typer.echo(markdown)


# ── health ────────────────────────────────────────────────────────────────────


@app.command()
def health() -> None:
    """Probe Shlink reachability and exit 0 (healthy) or 1 (unhealthy)."""
    from shlink.client import build_shlink_client_from_settings

    async def _probe() -> int:
        settings = get_settings()
        setup_logging(log_format="console", log_level="INFO")
        logger = structlog.stdlib.get_logger("bg-shlink-mcp.health")
        client = build_shlink_client_from_settings(settings)
        try:
            result = await client.health()
            logger.info("health.ok", status=result.get("status"), shlink=result)
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.error("health.failed", error=str(exc))
            return 1
        finally:
            await client.aclose()

    raise typer.Exit(asyncio.run(_probe()))


# ── entry ─────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    app()
