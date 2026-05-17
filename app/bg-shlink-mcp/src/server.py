"""
FastMCP server construction.

Wires Settings -> auth provider -> Shlink client -> OpenAPI spec -> tool surface.
Owns the lifespan (spec refresh task + client teardown).
"""

from __future__ import annotations

import asyncio
import string
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

import structlog

from auth.provider_factory import build_auth_provider
from config import AuthMode, Settings, get_settings
from logging_setup import print_banner, setup_logging, warn_no_auth
from shlink.client import build_shlink_client_from_settings
from shlink.openapi_loader import SpecCache
from shlink.tool_mapper import build_mcp_from_spec

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = structlog.stdlib.get_logger("bg-shlink-mcp.server")

try:
    __version__ = _pkg_version("bg-shlink-mcp")
except PackageNotFoundError:
    # Running from source without `pip install` (tests, local dev via src/ on PYTHONPATH).
    __version__ = "0.0.0+local"

SERVER_INSTRUCTIONS = (
    "Self-hosted Shlink URL shortener. Use these tools to create, list, edit, "
    "and analyse short links, manage tags and domains, and inspect visit "
    "analytics. The server talks to Shlink server-to-server with a single "
    "API key - your caller identity is authenticated via OAuth."
)


async def build_app(settings: Settings | None = None) -> "FastMCP":
    """Build a fully wired FastMCP instance. Called once per process."""
    settings = settings or get_settings()

    setup_logging(log_format=settings.log_format, log_level=settings.log_level)
    print_banner(
        version=__version__,
        environment=settings.environment.value,
        auth_mode=settings.auth_mode.value,
        public_base_url=str(settings.public_base_url),
    )
    if settings.auth_mode is AuthMode.NONE:
        warn_no_auth()

    _init_sentry_if_configured(settings)

    auth_provider = build_auth_provider(settings)
    shlink_client = build_shlink_client_from_settings(settings)
    spec_cache = SpecCache(
        settings.shlink_openapi_url,
        timeout=float(settings.shlink_http_timeout),
    )

    # Block startup on the first spec load - we cannot serve tools without it.
    loaded = await spec_cache.initial_load()
    logger.info(
        "server.bootstrap",
        spec_title=loaded.title,
        spec_version=loaded.info_version,
        operations=loaded.operation_count,
    )

    @asynccontextmanager
    async def lifespan(_app: "FastMCP") -> AsyncIterator[dict[str, object]]:
        refresh_task: asyncio.Task[None] | None = None
        if settings.shlink_openapi_refresh_interval > 0:
            refresh_task = asyncio.create_task(
                spec_cache.run_refresh_loop(settings.shlink_openapi_refresh_interval),
                name="openapi-refresh-loop",
            )
        try:
            yield {
                "settings": settings,
                "shlink_client": shlink_client,
                "spec_cache": spec_cache,
            }
        finally:
            if refresh_task and not refresh_task.done():
                refresh_task.cancel()
                try:
                    await refresh_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await shlink_client.aclose()
            logger.info("server.shutdown_complete")

    mcp = build_mcp_from_spec(
        name="bg-shlink-mcp",
        instructions=SERVER_INSTRUCTIONS,
        spec=spec_cache.current.spec,
        http_client=shlink_client.httpx_client,
        auth=auth_provider,
        lifespan=lifespan,
    )

    _register_healthz_route(mcp)
    _register_index_route(mcp, settings)
    return mcp


def _register_index_route(mcp: "FastMCP", settings: Settings) -> None:
    """
    Serve a human-readable status + quickstart page at /.

    Rendered once at startup from src/static/index.html using string.Template
    (so CSS braces don't collide with str.format placeholders). The fully
    substituted HTML is cached in the closure - no per-request rendering.
    """
    from starlette.responses import HTMLResponse

    template_path = Path(__file__).parent / "static" / "index.html"
    try:
        raw = template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("index.template_missing", path=str(template_path))
        return

    base_url = str(settings.public_base_url).rstrip("/")
    rendered = string.Template(raw).safe_substitute(
        version=__version__,
        protocol="MCP / Streamable HTTP",
        environment=settings.environment.value,
        auth_mode=settings.auth_mode.value,
        mcp_url=f"{base_url}/mcp",
    )

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def _index(_request) -> HTMLResponse:  # type: ignore[no-untyped-def]
        return HTMLResponse(
            rendered,
            headers={"Cache-Control": "public, max-age=60"},
        )


def _register_healthz_route(mcp: "FastMCP") -> None:
    """
    Expose /healthz that returns 200 OK as soon as the server is up.

    Distinct from Shlink's /health (which sits behind the OAuth wall) - this
    one is for container orchestrators (Docker HEALTHCHECK, k8s probes,
    Traefik liveness checks).
    """
    from starlette.responses import JSONResponse

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def _healthz(_request) -> JSONResponse:  # type: ignore[no-untyped-def]
        return JSONResponse({"status": "ok"}, status_code=200)


def _init_sentry_if_configured(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry.sdk_missing", hint="install sentry-sdk to enable error tracking")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment or settings.environment.value,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        release=__version__,
        send_default_pii=False,
    )
    logger.info(
        "sentry.initialized",
        environment=settings.sentry_environment or settings.environment.value,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
