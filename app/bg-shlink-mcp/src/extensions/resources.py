"""Register operator-defined resources & resource templates from `extensions.json`.

Each config entry becomes either:

* a `FunctionResource` — for static URIs (no `{name}` segments)
* a `ResourceTemplate` — for parameterised URIs (`shlink://short-url/{shortCode}`)

The detection is done by FastMCP's `mcp.add_resource()` when handed a
callable; we don't need to dispatch by shape ourselves. The shape of the
closure's signature (zero kwargs vs. one-or-more) tells FastMCP what to do.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP

from .config import ResourceConfig

logger = structlog.stdlib.get_logger("bg-shlink-mcp.extensions.resources")

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def register_resources(
    mcp: FastMCP,
    resources: list[ResourceConfig],
    http_client: httpx.AsyncClient,
) -> tuple[int, int]:
    """Register every resource entry.

    Returns a `(resource_count, template_count)` pair. As with prompts,
    failures on one entry don't abort the others.
    """
    resource_count = 0
    template_count = 0
    for cfg in resources:
        try:
            fn = _build_resource_function(cfg, http_client)
            mcp.resource(
                cfg.uri,
                name=cfg.name,
                title=cfg.title,
                description=cfg.description or None,
                mime_type=cfg.mime_type,
                tags=set(cfg.tags) if cfg.tags else None,
            )(fn)
            placeholders = _PLACEHOLDER_RE.findall(cfg.uri)
            if placeholders:
                template_count += 1
                logger.info("extensions.template_registered", uri=cfg.uri, params=placeholders)
            else:
                resource_count += 1
                logger.info("extensions.resource_registered", uri=cfg.uri)
        except Exception as exc:  # noqa: BLE001
            logger.error("extensions.resource_registration_failed", uri=cfg.uri, error=str(exc))
    return resource_count, template_count


def _build_resource_function(
    cfg: ResourceConfig,
    http_client: httpx.AsyncClient,
) -> Callable[..., Awaitable[Any]]:
    """Synthesise an async function that materialises one resource instance.

    For parameterised URIs the function takes keyword-only parameters
    matching the placeholders; FastMCP introspects them to know what the
    client must supply when expanding the template.
    """
    placeholders = _PLACEHOLDER_RE.findall(cfg.backend.path)
    method = cfg.backend.method
    backend_path = cfg.backend.path

    async def _runner(**kwargs: str) -> Any:
        # Substitute path placeholders. Values are URL-encoded by httpx
        # when it builds the request, so we just do plain string format.
        path = backend_path
        for name, value in kwargs.items():
            path = path.replace(f"{{{name}}}", str(value))
        response = await http_client.request(method, path)
        response.raise_for_status()
        # Resources may return JSON / text / bytes — FastMCP figures out
        # the right ResourceContent shape from the function's return value
        # and the declared mime_type.
        ctype = response.headers.get("content-type", "").lower()
        if "json" in ctype or cfg.mime_type == "application/json":
            return response.json()
        return response.text

    _runner.__name__ = f"resource_{cfg.name.lower().replace(' ', '_')}"
    _runner.__doc__ = cfg.description or None

    parameters = [
        inspect.Parameter(
            name=p,
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=str,
        )
        for p in placeholders
    ]
    _runner.__signature__ = inspect.Signature(parameters=parameters, return_annotation=Any)  # type: ignore[attr-defined]
    _runner.__annotations__ = {p: str for p in placeholders} | {"return": Any}
    return _runner
