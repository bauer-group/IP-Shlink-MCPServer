"""Bulk-export tasks — config-driven registration, class-based execution.

Why this file is bigger than `prompts.py` / `resources.py`: prompts and
resources are pure transformations of operator-supplied templates, so
"config-driven" gets us all the way. Tasks have actual procedural logic
(pagination, format conversion) that has to be Python — the config
controls *which* exports exist and *how they run*, but not *what they do*.

The split:

* `Exporter` — abstract base; one concrete subclass per data domain.
* `ShortUrlExporter` — pages through `/short-urls` and renders CSV or JSON.
* `register_tasks()` — walks `ExportTaskConfig` entries, picks the right
  Exporter class via `_EXPORTERS`, and wires a `@mcp.tool(task=...)`.

To add a new export (e.g. tags, domains), subclass `Exporter`, register it
in `_EXPORTERS`, and reference it from `extensions.json` by `exporter:
"my_key"`. No other code path changes.
"""

from __future__ import annotations

import csv
import io
import json
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from fastmcp.utilities.tasks import TaskConfig

from .config import ExportTaskConfig

logger = structlog.stdlib.get_logger("bg-shlink-mcp.extensions.tasks")


# ── Exporter classes ────────────────────────────────────────────────────────


class Exporter(ABC):
    """One bulk-data export domain. Subclasses know how to paginate and render."""

    def __init__(self, http_client: httpx.AsyncClient, page_size: int) -> None:
        self._client = http_client
        self._page_size = page_size

    @abstractmethod
    async def fetch_all(self) -> list[dict[str, Any]]:
        """Walk every page of the source endpoint and return the flattened items."""

    @staticmethod
    def to_json(items: list[dict[str, Any]]) -> str:
        return json.dumps(items, ensure_ascii=False, indent=2)

    @staticmethod
    def to_csv(items: list[dict[str, Any]]) -> str:
        if not items:
            return ""
        # Build the column set from the *union* of keys across rows, sorted for
        # determinism. Nested objects (tags, meta) are JSON-encoded into the
        # cell so the CSV stays flat — round-tripping is the caller's problem.
        columns = sorted({k for item in items for k in item})
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow({c: _cell(item.get(c)) for c in columns})
        return buffer.getvalue()


def _cell(value: Any) -> str:
    """Render one CSV cell value — flatten lists/dicts via JSON."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


class ShortUrlExporter(Exporter):
    """Paginate `/short-urls` and emit a flat list of objects."""

    async def fetch_all(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            response = await self._client.get(
                "/short-urls",
                params={"page": page, "itemsPerPage": self._page_size},
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


# Registry: maps the `exporter` key in extensions.json to the implementation.
_EXPORTERS: dict[str, type[Exporter]] = {
    "short_urls": ShortUrlExporter,
}


# ── Task registration ───────────────────────────────────────────────────────


def register_tasks(
    mcp: FastMCP,
    tasks: list[ExportTaskConfig],
    http_client: httpx.AsyncClient,
) -> int:
    """Register every export-task entry as a long-running MCP tool."""
    registered = 0
    for cfg in tasks:
        try:
            exporter_cls = _EXPORTERS.get(cfg.exporter)
            if exporter_cls is None:
                logger.error(
                    "extensions.task_unknown_exporter",
                    name=cfg.name,
                    exporter=cfg.exporter,
                    known=list(_EXPORTERS),
                )
                continue
            _register_one_task(mcp, cfg, exporter_cls, http_client)
            registered += 1
            logger.info("extensions.task_registered", name=cfg.name, formats=cfg.formats)
        except Exception as exc:  # noqa: BLE001
            logger.error("extensions.task_registration_failed", name=cfg.name, error=str(exc))
    return registered


def _register_one_task(
    mcp: FastMCP,
    cfg: ExportTaskConfig,
    exporter_cls: type[Exporter],
    http_client: httpx.AsyncClient,
) -> None:
    task_config = TaskConfig(
        mode=cfg.task.mode,
        poll_interval=timedelta(seconds=cfg.task.poll_interval_seconds),
    )

    # Build the closure with a tight, typed signature so FastMCP can derive
    # the input schema (Literal["csv","json"] becomes an enum the AI sees).
    allowed_formats = tuple(cfg.formats)
    default_format = allowed_formats[0]
    page_size = cfg.page_size

    async def _runner(format: str = default_format) -> dict[str, Any]:
        if format not in allowed_formats:
            raise ValueError(
                f"unknown format {format!r}; allowed: {list(allowed_formats)}"
            )
        exporter = exporter_cls(http_client=http_client, page_size=page_size)
        items = await exporter.fetch_all()
        if format == "csv":
            body = exporter.to_csv(items)
            mime = "text/csv"
        else:
            body = exporter.to_json(items)
            mime = "application/json"
        return {
            "format": format,
            "mime_type": mime,
            "item_count": len(items),
            "content": body,
        }

    _runner.__name__ = cfg.name
    _runner.__doc__ = cfg.description or None

    mcp.tool(
        name=cfg.name,
        title=cfg.title,
        description=cfg.description or None,
        tags=set(cfg.tags) if cfg.tags else None,
        task=task_config,
    )(_runner)
