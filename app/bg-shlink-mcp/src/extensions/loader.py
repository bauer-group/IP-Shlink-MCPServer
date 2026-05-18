"""Entry point — reads `extensions.json` and registers every entry on the FastMCP instance.

Wired from `server.py` after `build_mcp_from_spec` so the OpenAPI-derived
tool surface is already in place; extensions are layered on top and may
co-exist with the auto-generated tools (e.g. `get_short_url` the tool and
`shlink://short-url/{shortCode}` the template).

Failure policy:

* **Unreadable / invalid config** → propagates `ExtensionsConfigError` so
  the server fails fast at boot. The operator misconfigured something
  global; we don't want to start half-broken.
* **One bad entry within a valid config** → logged, skipped, the rest
  still register. Same pattern as `tool_mapper._make_component_fn`.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import structlog
from fastmcp import FastMCP

from .config import ExtensionsConfigError, load_config
from .prompts import register_prompts
from .resources import register_resources
from .tasks import register_tasks

logger = structlog.stdlib.get_logger("bg-shlink-mcp.extensions")


def load_extensions(
    mcp: FastMCP,
    *,
    config_source: str,
    http_client: httpx.AsyncClient,
    required: bool = False,
) -> dict[str, int]:
    """Read, validate, and register every extension entry.

    Args:
        mcp: FastMCP instance returned by `build_mcp_from_spec`.
        config_source: `file://` URL or bare path to `extensions.json`.
        http_client: The same long-lived httpx client used by the OpenAPI
            tools — extension resources/tasks reuse it for outbound calls.
        required: If True and the config file is missing, raise. If False
            (default), a missing file is logged and skipped — handy when
            someone runs the image without mounting an extensions volume.

    Returns:
        A dict with counters for telemetry / startup logging:
        `{"prompts": N, "resources": N, "templates": N, "tasks": N}`.
    """
    if not _config_exists(config_source):
        if required:
            raise ExtensionsConfigError(
                f"extensions config required but not found at {config_source}"
            )
        logger.info("extensions.skipped_no_config", source=config_source)
        return {"prompts": 0, "resources": 0, "templates": 0, "tasks": 0}

    config = load_config(config_source)

    counts = {
        "prompts": register_prompts(mcp, config.prompts),
        **dict(zip(("resources", "templates"), register_resources(mcp, config.resources, http_client))),
        "tasks": register_tasks(mcp, config.tasks, http_client),
    }
    logger.info("extensions.loaded", source=config_source, **counts)
    return counts


def _config_exists(source: str) -> bool:
    """Cheap existence check that handles `file://` and bare paths."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(source)
    if parsed.scheme == "file":
        # Use the same Path resolver the config loader uses (Path.from_uri on 3.13+).
        from_uri = getattr(Path, "from_uri", None)
        if from_uri is not None:
            try:
                return from_uri(source).exists()
            except (ValueError, OSError):
                pass
        return Path(unquote(parsed.path.lstrip("/"))).exists()
    # Bare path — single-letter "scheme" is a Windows drive letter, not a URL scheme.
    if not parsed.scheme or (len(parsed.scheme) == 1 and parsed.scheme.isalpha()):
        return Path(source).exists()
    return False
