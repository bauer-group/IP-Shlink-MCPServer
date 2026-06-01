"""Tests for the bulk-export task (the one Shlink-specific python tool source).

Covers the CSV/JSON rendering helpers, the /short-urls pagination loop, and that
the task registers as a single tool named ``export_short_urls``.
"""

from __future__ import annotations

import json
from typing import Any

from server import (
    _cell,
    _fetch_all_short_urls,
    _to_csv,
    _to_json,
    register_export_task,
)

# ── fake upstream context ─────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeCtx:
    """Stands in for bg-mcpcore's ToolContext — records calls, serves pages."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "path": path, **kwargs})
        page = int(kwargs["params"]["page"])
        return _FakeResponse(self._pages[page - 1])


def _page(data: list[dict[str, Any]], current: int, total: int) -> dict[str, Any]:
    return {"shortUrls": {"data": data, "pagination": {"currentPage": current, "pagesCount": total}}}


# ── rendering helpers ─────────────────────────────────────────────────────────


def test_cell_flattens_values() -> None:
    assert _cell(None) == ""
    assert _cell("x") == "x"
    assert _cell(42) == "42"
    assert _cell(True) == "True"
    # Nested values are JSON-encoded with json.dumps' default separators (spaces
    # after ',' and ':') — matching the pre-migration exporter byte-for-byte.
    assert _cell(["a", "b"]) == '["a", "b"]'


def test_to_csv_unions_keys_and_flattens() -> None:
    items = [{"a": 1, "tags": ["x", "y"]}, {"a": 2, "b": 3}]
    out = _to_csv(items)
    header = out.splitlines()[0]
    # Columns are the sorted union of all row keys.
    assert header == "a,b,tags"
    # The nested list is JSON-encoded into its cell, then CSV-quoted ("" escapes).
    assert '"[""x"", ""y""]"' in out


def test_to_csv_empty_is_empty_string() -> None:
    assert _to_csv([]) == ""


def test_to_json_pretty() -> None:
    items = [{"a": 1}]
    assert _to_json(items) == json.dumps(items, ensure_ascii=False, indent=2)


# ── pagination ────────────────────────────────────────────────────────────────


async def test_fetch_all_pages_through_everything() -> None:
    ctx = _FakeCtx(
        [
            _page([{"shortCode": "a"}, {"shortCode": "b"}], current=1, total=2),
            _page([{"shortCode": "c"}], current=2, total=2),
        ]
    )
    items = await _fetch_all_short_urls(ctx)  # type: ignore[arg-type]
    assert [i["shortCode"] for i in items] == ["a", "b", "c"]
    assert len(ctx.calls) == 2
    assert ctx.calls[0]["params"]["itemsPerPage"] == 200


async def test_fetch_all_stops_on_single_page() -> None:
    ctx = _FakeCtx([_page([{"shortCode": "a"}], current=1, total=1)])
    items = await _fetch_all_short_urls(ctx)  # type: ignore[arg-type]
    assert len(items) == 1
    assert len(ctx.calls) == 1


# ── registration ──────────────────────────────────────────────────────────────


async def test_register_export_task_registers_one_tool() -> None:
    from fastmcp import FastMCP

    mcp = FastMCP(name="t")
    ctx = _FakeCtx([])  # not invoked at registration time
    count = register_export_task(mcp, ctx)  # type: ignore[arg-type]
    assert count == 1
    names = [getattr(t, "name", "") for t in await mcp.list_tools()]
    assert "export_short_urls" in names
