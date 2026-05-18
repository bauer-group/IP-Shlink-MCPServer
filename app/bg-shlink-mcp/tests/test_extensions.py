"""Tests for the operator-defined extensions package.

Two layers:

1. **Config validation** — `extensions.json` shape, Pydantic constraints,
   the cross-field validators (placeholder/argument alignment, unique names).
2. **Registration** — that each section walks through to its FastMCP
   factory, that bad entries are logged-and-skipped without aborting the
   rest, and that the synthesised function signatures introspect correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from extensions.config import (
    ExtensionsConfig,
    ExtensionsConfigError,
    PromptArgumentConfig,
    PromptConfig,
    ResourceConfig,
    ExportTaskConfig,
    load_config,
)
from extensions.prompts import _build_prompt_function
from extensions.resources import _build_resource_function
from extensions.tasks import ShortUrlExporter


# ── Pydantic schema ─────────────────────────────────────────────────────────


def test_empty_config_is_valid():
    """A completely empty extensions block is fine — no extensions, no problem."""
    cfg = ExtensionsConfig.model_validate({})
    assert cfg.prompts == [] and cfg.resources == [] and cfg.tasks == []


def test_prompt_placeholders_must_match_arguments():
    """A `${name}` in the template that has no declared argument fails validation."""
    with pytest.raises(Exception) as exc:
        PromptConfig.model_validate({
            "name": "p",
            "template": "hello ${missing}",
            "arguments": [],
        })
    assert "undeclared placeholders" in str(exc.value)


def test_prompt_unused_argument_rejected():
    """Declaring an argument that the template never references is also a config bug."""
    with pytest.raises(Exception) as exc:
        PromptConfig.model_validate({
            "name": "p",
            "template": "static text",
            "arguments": [{"name": "unused", "type": "string"}],
        })
    assert "never used in the template" in str(exc.value)


def test_prompt_argument_default_must_match_type():
    """`default: 3` with `type: 'string'` is a contract violation."""
    with pytest.raises(Exception):
        PromptArgumentConfig.model_validate({"name": "x", "type": "string", "default": 3})


def test_prompt_integer_default_rejects_boolean():
    """Python quirk: bool is a subclass of int — we guard against it explicitly."""
    with pytest.raises(Exception):
        PromptArgumentConfig.model_validate({"name": "x", "type": "integer", "default": True})


def test_resource_uri_and_path_placeholders_must_align():
    """URI `{shortCode}` and backend path `{slug}` is a config bug — they must match."""
    with pytest.raises(Exception) as exc:
        ResourceConfig.model_validate({
            "uri": "shlink://x/{shortCode}",
            "name": "x",
            "backend": {"method": "GET", "path": "/y/{slug}"},
        })
    assert "do not match" in str(exc.value)


def test_resource_requires_shlink_scheme():
    with pytest.raises(Exception):
        ResourceConfig.model_validate({
            "uri": "http://nope/x",
            "name": "x",
            "backend": {"method": "GET", "path": "/x"},
        })


def test_unique_names_within_section():
    """Two prompts with the same name fails at the top level — clients can't disambiguate them."""
    with pytest.raises(Exception) as exc:
        ExtensionsConfig.model_validate({
            "prompts": [
                {"name": "p", "template": "a"},
                {"name": "p", "template": "b"},
            ],
        })
    assert "duplicate prompt" in str(exc.value).lower()


def test_task_formats_cannot_be_empty():
    with pytest.raises(Exception):
        ExportTaskConfig.model_validate({
            "name": "t",
            "exporter": "short_urls",
            "formats": [],
        })


def test_unknown_field_rejected():
    """`extra=forbid` — typos in keys fail at boot, not silently in production."""
    with pytest.raises(Exception):
        PromptConfig.model_validate({
            "name": "p",
            "template": "static",
            "typo_argumetns": [],   # ← intentional typo
        })


# ── load_config (JSON → Pydantic) ───────────────────────────────────────────


def test_load_config_from_file(tmp_path: Path):
    payload = {
        "prompts": [{"name": "p", "template": "hello ${x}", "arguments": [{"name": "x"}]}],
    }
    file = tmp_path / "extensions.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    cfg = load_config(str(file))
    assert len(cfg.prompts) == 1


def test_load_config_strips_schema_reference(tmp_path: Path):
    """`$schema` is for IDE autocompletion; the loader strips it before validation."""
    payload = {
        "$schema": "./extensions.schema.json",
        "prompts": [],
    }
    file = tmp_path / "extensions.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    cfg = load_config(str(file))
    assert cfg.prompts == []


def test_load_config_invalid_json_raises_extensions_error(tmp_path: Path):
    file = tmp_path / "bad.json"
    file.write_text("{not json", encoding="utf-8")
    with pytest.raises(ExtensionsConfigError):
        load_config(str(file))


def test_load_config_remote_scheme_rejected():
    with pytest.raises(ExtensionsConfigError):
        load_config("https://example.com/extensions.json")


def test_real_extensions_json_is_valid():
    """The shipped catalogue must always validate — guards against config drift."""
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(str(root / "extensions" / "extensions.json"))
    assert any(p.name == "top_visits_report" for p in cfg.prompts)
    assert any("short-url/{shortCode}" in r.uri for r in cfg.resources)
    assert any(t.name == "export_short_urls" for t in cfg.tasks)


# ── Synthesised callable signatures ─────────────────────────────────────────


def test_built_prompt_function_has_real_signature():
    """FastMCP introspects this signature to derive the prompt arg schema."""
    import inspect

    cfg = PromptConfig.model_validate({
        "name": "p",
        "template": "page ${page} of ${count}",
        "arguments": [
            {"name": "page", "type": "integer", "default": 1},
            {"name": "count", "type": "integer", "default": 10},
        ],
    })
    fn = _build_prompt_function(cfg)
    sig = inspect.signature(fn)
    assert list(sig.parameters) == ["page", "count"]
    assert sig.parameters["page"].annotation is int
    assert sig.parameters["count"].default == 10


def test_built_prompt_function_renders_template():
    cfg = PromptConfig.model_validate({
        "name": "p",
        "template": "top ${limit} over ${days} days",
        "arguments": [
            {"name": "limit", "type": "integer", "default": 20},
            {"name": "days", "type": "integer", "default": 30},
        ],
    })
    fn = _build_prompt_function(cfg)
    assert fn(limit=5, days=7) == "top 5 over 7 days"


async def test_built_resource_function_substitutes_path_placeholders():
    """Resource template runner must replace `{shortCode}` in the backend path."""
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"shortCode": "abc"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://shlink/rest/v3",
    )
    cfg = ResourceConfig.model_validate({
        "uri": "shlink://short-url/{shortCode}",
        "name": "x",
        "backend": {"method": "GET", "path": "/short-urls/{shortCode}"},
    })
    fn = _build_resource_function(cfg, client)
    result = await fn(shortCode="abc")
    assert result == {"shortCode": "abc"}
    assert "/short-urls/abc" in captured["url"]


# ── Tasks: exporter classes ─────────────────────────────────────────────────


def test_short_url_exporter_csv_flattens_nested_fields():
    items = [
        {"shortCode": "a", "longUrl": "https://x", "tags": ["one", "two"]},
        {"shortCode": "b", "longUrl": "https://y", "tags": []},
    ]
    csv_text = ShortUrlExporter.to_csv(items)
    # Header is alphabetised
    assert csv_text.splitlines()[0] == "longUrl,shortCode,tags"
    # Lists are JSON-encoded into the cell
    assert '"[""one"", ""two""]"' in csv_text or '[""one"", ""two""]' in csv_text


def test_short_url_exporter_csv_empty_input():
    assert ShortUrlExporter.to_csv([]) == ""


def test_short_url_exporter_json_preserves_unicode():
    items = [{"longUrl": "https://example.com/ümlaut"}]
    assert "ümlaut" in ShortUrlExporter.to_json(items)


async def test_short_url_exporter_paginates_until_done():
    """Walks every page; stops when `currentPage >= pagesCount`."""
    pages_seen: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        pages_seen.append(page)
        return httpx.Response(200, json={"shortUrls": {
            "data": [{"shortCode": f"page{page}item1"}],
            "pagination": {"currentPage": page, "pagesCount": 3},
        }})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://shlink/rest/v3",
    )
    exporter = ShortUrlExporter(http_client=client, page_size=50)
    items = await exporter.fetch_all()
    assert pages_seen == [1, 2, 3]
    assert len(items) == 3
