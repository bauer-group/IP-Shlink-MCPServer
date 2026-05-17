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
from urllib.parse import unquote, urljoin, urlparse
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
    Fetch, parse, and dereference an OpenAPI spec. Raises SpecLoadError on failure.

    Accepts:
      - http(s)://host/path/spec.json|yaml
      - file:///absolute/path

    External `$ref` pointers (e.g. `"$ref": "paths/foo.json"`) are resolved
    relative to the spec's own URI so unbundled-modular specs (like Shlink's
    canonical swagger.json) work at runtime. Internal `#/...` refs are left
    alone — FastMCP handles those itself. Bundled specs are a no-op for the
    resolver (it walks the tree, finds no externals, returns unchanged).
    """
    spec = await _fetch_and_parse(source, timeout=timeout)

    if not isinstance(spec, dict):
        raise SpecLoadError(f"OpenAPI spec at {source} is not a JSON object")
    if "paths" not in spec:
        raise SpecLoadError(f"OpenAPI spec at {source} is missing the `paths` section")

    try:
        spec = await _resolve_external_refs(spec, source, timeout=timeout)
    except SpecLoadError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface the chain with context
        raise SpecLoadError(
            f"Failed to resolve external $refs starting from {source}: {exc}"
        ) from exc

    operation_count = _count_operations(spec)
    if operation_count == 0:
        # A spec without operations produces zero MCP tools — the server would
        # boot but serve nothing. This usually means external $refs in `paths`
        # weren't resolved (e.g. the modular Shlink swagger.json wasn't bundled
        # before being baked into the image). Fail fast at startup instead of
        # silently shipping an empty tool surface.
        raise SpecLoadError(
            f"OpenAPI spec at {source} has 0 operations — `paths` is empty or "
            f"contains only unresolved $refs. Did the build-time bundling step run?"
        )

    info = spec.get("info", {}) if isinstance(spec.get("info"), dict) else {}
    return LoadedSpec(
        spec=spec,
        source=source,
        info_version=info.get("version"),
        title=info.get("title"),
        operation_count=operation_count,
    )


async def _fetch_and_parse(uri: str, *, timeout: float) -> Any:
    """Fetch a JSON/YAML document from any supported URI scheme and parse it.

    Centralised so the top-level loader and the recursive $ref resolver share
    one fetch/parse path — keeps error messages and YAML-fallback consistent.
    """
    parsed = urlparse(uri)
    if parsed.scheme in ("http", "https"):
        raw = await _fetch_remote(uri, timeout=timeout)
    elif parsed.scheme == "file":
        raw = _read_local(_file_url_to_path(uri))
    elif not parsed.scheme and os.path.exists(uri):
        raw = _read_local(uri)
    else:
        raise SpecLoadError(f"Unsupported spec source scheme: {uri!r}")

    try:
        return _parse(raw)
    except ValueError as exc:
        raise SpecLoadError(f"Failed to parse OpenAPI spec from {uri}: {exc}") from exc


def _resolve_ref_uri(base: str, ref: str) -> str:
    """Resolve a relative `$ref` against a base URI, preserving the base's scheme.

    For http(s)://: stdlib's `urljoin` handles "../", "/", and bare-filename
    relatives correctly. For file:// and bare paths: parent-directory + ref,
    normalised. Returns an absolute URI string ready for `_fetch_and_parse`.
    """
    parsed = urlparse(base)
    if parsed.scheme in ("http", "https"):
        return urljoin(base, ref)
    if parsed.scheme == "file":
        base_path = Path(_file_url_to_path(base))
        return (base_path.parent / ref).resolve().as_uri()
    # bare filesystem path
    base_path = Path(base)
    return str((base_path.parent / ref).resolve())


async def _resolve_external_refs(
    node: Any,
    current_uri: str,
    *,
    timeout: float,
    seen: tuple[str, ...] = (),
) -> Any:
    """Recursively inline external `$ref` pointers found anywhere in `node`.

    Only external refs (those without a `#`-prefix) are resolved — internal
    `#/components/...` refs are left in place because FastMCP's OpenAPI
    provider resolves them during component generation. `seen` carries the
    fetch-chain so a malformed spec with circular external refs raises with
    a useful trace instead of recursing until the stack runs out.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref and not ref.startswith("#"):
            ref_path, _, fragment = ref.partition("#")
            target_uri = _resolve_ref_uri(current_uri, ref_path)
            if target_uri in seen:
                chain = " -> ".join((*seen, target_uri))
                raise SpecLoadError(f"Circular external $ref chain: {chain}")
            sub = await _fetch_and_parse(target_uri, timeout=timeout)
            if fragment:
                for part in fragment.lstrip("/").split("/"):
                    if not isinstance(sub, dict):
                        raise SpecLoadError(
                            f"Fragment {fragment!r} cannot traverse non-object node "
                            f"while resolving {ref!r} from {current_uri}"
                        )
                    if part not in sub:
                        raise SpecLoadError(
                            f"Fragment part {part!r} not found in {target_uri} "
                            f"(resolving {ref!r} from {current_uri})"
                        )
                    sub = sub[part]
            return await _resolve_external_refs(
                sub, target_uri, timeout=timeout, seen=(*seen, target_uri),
            )
        return {
            key: await _resolve_external_refs(value, current_uri, timeout=timeout, seen=seen)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [
            await _resolve_external_refs(item, current_uri, timeout=timeout, seen=seen)
            for item in node
        ]
    return node


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
