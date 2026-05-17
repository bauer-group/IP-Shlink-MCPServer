"""
Shlink OpenAPI spec loader.

Fetches the Shlink OpenAPI 3 spec once at startup, caches it in memory, and
optionally refreshes on a fixed interval. A refresh failure NEVER tears down
the cached spec - the previous (working) version stays in place until the
next successful fetch.

Supports two sources transparently:
  - https://...   - remote fetch via httpx
  - file://...    - local file (for air-gapped or pinned-version deployments)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import httpx
import structlog

logger = structlog.stdlib.get_logger("bg-shlink-mcp.openapi")


@dataclass
class LoadedSpec:
    """A successfully loaded OpenAPI spec with provenance metadata."""

    spec: dict[str, Any]
    source: str
    info_version: str | None
    title: str | None
    operation_count: int


class SpecLoadError(RuntimeError):
    """Raised when the spec cannot be loaded for the first time."""


async def load_spec(source: str, *, timeout: float = 30.0) -> LoadedSpec:
    """
    Fetch and parse the OpenAPI spec. Raises SpecLoadError on failure.

    Accepts:
      - http(s)://host/path/spec.json|yaml
      - file:///absolute/path
    """
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        raw = await _fetch_remote(source, timeout=timeout)
    elif parsed.scheme == "file":
        raw = _read_local(_file_url_to_path(source))
    elif not parsed.scheme and os.path.exists(source):
        raw = _read_local(source)
    else:
        raise SpecLoadError(f"Unsupported spec source scheme: {source!r}")

    try:
        spec = _parse(raw)
    except ValueError as exc:
        raise SpecLoadError(f"Failed to parse OpenAPI spec from {source}: {exc}") from exc

    if not isinstance(spec, dict):
        raise SpecLoadError(f"OpenAPI spec at {source} is not a JSON object")
    if "paths" not in spec:
        raise SpecLoadError(f"OpenAPI spec at {source} is missing the `paths` section")

    info = spec.get("info", {}) if isinstance(spec.get("info"), dict) else {}
    return LoadedSpec(
        spec=spec,
        source=source,
        info_version=info.get("version"),
        title=info.get("title"),
        operation_count=_count_operations(spec),
    )


async def _fetch_remote(url: str, *, timeout: float) -> str:
    headers = {
        "Accept": "application/json, application/yaml, text/yaml",
        "User-Agent": "bg-shlink-mcp/0.1.0",
    }
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _read_local(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _file_url_to_path(url: str) -> str:
    """Convert file:// URL to a local filesystem path, cross-platform."""
    # Python 3.13+ has Path.from_uri() which handles the cross-platform edge cases
    # (drive letters, UNC shares, percent-encoding). Use it when available.
    from_uri = getattr(Path, "from_uri", None)
    if from_uri is not None:
        try:
            return str(from_uri(url))
        except (ValueError, OSError):
            pass

    parsed = urlparse(url)
    raw_path = parsed.path
    # On Windows, a user may write `file://C:\path` (netloc parsed as `C:`); treat it as path.
    if parsed.netloc and not re.match(r"^[a-zA-Z]:$", parsed.netloc) and parsed.netloc != "localhost":
        return r"\\" + parsed.netloc + url2pathname(raw_path)
    if parsed.netloc:
        raw_path = "/" + parsed.netloc + raw_path
    return url2pathname(unquote(raw_path))


def _parse(raw: str) -> dict[str, Any]:
    """Try JSON first, fall back to YAML if available."""
    raw_stripped = raw.lstrip()
    if raw_stripped.startswith("{"):
        return json.loads(raw)
    # Try JSON anyway - many "yaml" endpoints actually serve JSON.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError(
            "spec appears to be YAML but pyyaml is not installed; "
            "either install pyyaml or use a JSON spec URL"
        ) from exc
    return yaml.safe_load(raw)


def _count_operations(spec: dict[str, Any]) -> int:
    paths = spec.get("paths", {}) or {}
    if not isinstance(paths, dict):
        return 0
    verbs = {"get", "post", "put", "patch", "delete", "head", "options"}
    return sum(
        1
        for path_item in paths.values()
        if isinstance(path_item, dict)
        for method in path_item
        if method.lower() in verbs
    )


# ── In-memory cache + refresh loop ───────────────────────────────────────────


class SpecCache:
    """
    Holds the active spec and offers an asyncio task that refreshes it.

    Designed so a transient network blip during refresh does NOT invalidate
    the cached spec - tools keep working off the last good copy until the
    next successful fetch.
    """

    def __init__(self, source: str, *, timeout: float = 30.0) -> None:
        self._source = source
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._current: LoadedSpec | None = None

    @property
    def current(self) -> LoadedSpec:
        if self._current is None:
            raise RuntimeError("SpecCache.current accessed before initial load")
        return self._current

    async def initial_load(self) -> LoadedSpec:
        async with self._lock:
            self._current = await load_spec(self._source, timeout=self._timeout)
            logger.info(
                "openapi.loaded",
                source=self._source,
                spec_title=self._current.title,
                spec_version=self._current.info_version,
                operations=self._current.operation_count,
            )
            return self._current

    async def refresh_once(self) -> bool:
        """Re-fetch the spec. Returns True on success, False on any failure."""
        try:
            new_spec = await load_spec(self._source, timeout=self._timeout)
        except Exception as exc:
            logger.warning("openapi.refresh_failed", source=self._source, error=str(exc))
            return False

        async with self._lock:
            previous = self._current
            self._current = new_spec
        logger.info(
            "openapi.refreshed",
            source=self._source,
            spec_version=new_spec.info_version,
            previous_version=previous.info_version if previous else None,
            operations=new_spec.operation_count,
        )
        return True

    async def run_refresh_loop(self, interval_seconds: int) -> None:
        """Background task - cancelled on shutdown by the lifespan manager."""
        if interval_seconds <= 0:
            return
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self.refresh_once()
            except asyncio.CancelledError:
                logger.info("openapi.refresh_loop_cancelled")
                raise
            except Exception as exc:
                # Defensive - the loop must never die silently.
                logger.exception("openapi.refresh_loop_unexpected_error", error=str(exc))
                await asyncio.sleep(min(interval_seconds, 60))
