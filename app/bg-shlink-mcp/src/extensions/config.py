"""Pydantic schema for `extensions.json`.

The config has three top-level sections — `prompts`, `resources`, `tasks` —
each holding a list of operator-defined components. Every section is
independently validated and may be empty. The loader (`loader.py`) walks
this validated object and dispatches to per-section registration helpers.

Design choices:

* JSON over YAML — stdlib parser, faster, no dependency, easier to validate
  with a `$schema` URL in IDEs.
* `extra = "forbid"` everywhere — typos in keys fail loudly at boot rather
  than silently producing an extension that does nothing.
* Backend paths are SHLINK-RELATIVE (e.g. `/short-urls/{shortCode}`).
  `/rest/v3` is the httpx base_url's job — see `_normalize_spec_for_v3`
  in `shlink/tool_mapper.py` for the same convention applied to the
  generated tool surface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Allowed names for prompt arguments and template placeholders — kept tight
# so a typo in `extensions.json` can't smuggle in arbitrary Python identifiers.
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Argument-type → Python annotation. Keep this table small on purpose; if a
# new type genuinely belongs here, add it once and propagate via the loader.
PYTHON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


class _StrictBase(BaseModel):
    """Pydantic base that forbids unknown fields — typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


# ── Prompts ─────────────────────────────────────────────────────────────────


class PromptArgumentConfig(_StrictBase):
    """One parameter of a prompt template."""

    name: str = Field(..., description="Identifier used as both the argument name and the `${name}` placeholder.")
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = ""
    required: bool = False
    default: str | int | float | bool | None = None

    @field_validator("name")
    @classmethod
    def _valid_identifier(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"prompt argument name {v!r} is not a valid Python identifier")
        return v

    @model_validator(mode="after")
    def _check_default_type(self) -> "PromptArgumentConfig":
        """If a default is given, it must match the declared type."""
        if self.default is None:
            return self
        expected = PYTHON_TYPE_MAP[self.type]
        # bool is a subclass of int — guard explicitly so type='integer' doesn't accept True.
        if expected is int and isinstance(self.default, bool):
            raise ValueError(f"argument {self.name!r}: default {self.default!r} does not match type 'integer'")
        if not isinstance(self.default, expected):
            raise ValueError(
                f"argument {self.name!r}: default {self.default!r} does not match type {self.type!r}"
            )
        return self


class PromptConfig(_StrictBase):
    """One operator-defined prompt — multi-tool workflow as a template."""

    name: str = Field(..., description="Tool-style name shown to the AI client.")
    title: str | None = None
    description: str = ""
    arguments: list[PromptArgumentConfig] = Field(default_factory=list)
    template: str = Field(
        ...,
        description="Prompt body. Use `${name}` placeholders that resolve to the matching argument value.",
    )
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"prompt name {v!r} is not a valid identifier")
        return v

    @model_validator(mode="after")
    def _placeholders_match_arguments(self) -> "PromptConfig":
        """Every `${name}` placeholder must have a matching argument; unused arguments are also rejected."""
        # string.Template's pattern: ${name}, $name, $$ (escape)
        placeholders = set(re.findall(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}", self.template))
        placeholders |= set(re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", self.template))
        declared = {a.name for a in self.arguments}
        missing = placeholders - declared
        extra = declared - placeholders
        if missing:
            raise ValueError(
                f"prompt {self.name!r}: template references undeclared placeholders {sorted(missing)}"
            )
        if extra:
            raise ValueError(
                f"prompt {self.name!r}: arguments {sorted(extra)} are declared but never used in the template"
            )
        return self


# ── Resources ───────────────────────────────────────────────────────────────


class BackendCall(_StrictBase):
    """How the resource resolves to a Shlink REST call."""

    method: Literal["GET"] = "GET"  # only GETs make sense as resources
    path: str = Field(..., description="Shlink path (without /rest/v3). May contain `{name}` placeholders matching URI template segments.")

    @field_validator("path")
    @classmethod
    def _starts_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"backend path {v!r} must start with '/'")
        return v


class ResourceConfig(_StrictBase):
    """One operator-defined resource (or resource template if URI is parameterised)."""

    uri: str = Field(..., description="`shlink://...` URI. `{name}` segments make it a template.")
    name: str
    title: str | None = None
    description: str = ""
    mime_type: str = "application/json"
    backend: BackendCall
    tags: list[str] = Field(default_factory=list)

    @field_validator("uri")
    @classmethod
    def _shlink_scheme(cls, v: str) -> str:
        if not v.startswith("shlink://"):
            raise ValueError(f"resource uri {v!r} must use the shlink:// scheme")
        return v

    @model_validator(mode="after")
    def _placeholders_match(self) -> "ResourceConfig":
        """URI placeholders must line up with backend-path placeholders."""
        uri_params = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", self.uri))
        path_params = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", self.backend.path))
        if uri_params != path_params:
            raise ValueError(
                f"resource {self.uri!r}: URI placeholders {sorted(uri_params)} do not match "
                f"backend-path placeholders {sorted(path_params)}"
            )
        return self


# ── Tasks (export) ──────────────────────────────────────────────────────────


class TaskExecutionConfig(_StrictBase):
    """How a task runs — surfaces FastMCP TaskConfig knobs to operators.

    Note: TTL is intentionally NOT here. FastMCP's `TaskConfig` only exposes
    `mode` and `poll_interval`; the task TTL is a per-call client knob
    (`call_tool(..., ttl=...)`) and has no server-side default override.
    Adding `ttl_seconds` here would be cargo-cult config that does nothing.
    """

    mode: Literal["optional", "required"] = "optional"
    poll_interval_seconds: float = Field(default=5.0, ge=0.5, le=60.0)


class ExportTaskConfig(_StrictBase):
    """One bulk-export task. The `exporter` key selects the Python implementation."""

    name: str = Field(..., description="Tool name shown to the AI client (snake_case).")
    title: str | None = None
    description: str = ""
    exporter: Literal["short_urls"] = Field(
        ...,
        description="Implementation key. Currently only `short_urls` is wired up; add new exporters in `tasks.py`.",
    )
    formats: list[Literal["csv", "json"]] = Field(default_factory=lambda: ["csv", "json"])
    page_size: int = Field(default=100, ge=10, le=1000)
    task: TaskExecutionConfig = Field(default_factory=TaskExecutionConfig)
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"task name {v!r} is not a valid identifier")
        return v

    @field_validator("formats")
    @classmethod
    def _no_duplicate_formats(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError(f"duplicate entries in formats: {v}")
        if not v:
            raise ValueError("at least one format must be declared")
        return v


# ── Top-level container ─────────────────────────────────────────────────────


class ExtensionsConfig(_StrictBase):
    """Root of `extensions.json`."""

    prompts: list[PromptConfig] = Field(default_factory=list)
    resources: list[ResourceConfig] = Field(default_factory=list)
    tasks: list[ExportTaskConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> "ExtensionsConfig":
        """Prompt/task names and resource URIs must be unique within their section."""
        for section_label, items in [
            ("prompt", [p.name for p in self.prompts]),
            ("task", [t.name for t in self.tasks]),
            ("resource", [r.uri for r in self.resources]),
        ]:
            if len(items) != len(set(items)):
                duplicates = [x for x in items if items.count(x) > 1]
                raise ValueError(
                    f"duplicate {section_label} identifiers: {sorted(set(duplicates))}"
                )
        return self


# ── I/O ─────────────────────────────────────────────────────────────────────


class ExtensionsConfigError(RuntimeError):
    """Raised when `extensions.json` is unreadable or invalid."""


def load_config(source: str) -> ExtensionsConfig:
    """Read and validate `extensions.json` from a `file://` or local path.

    Remote (`http://`) sources are deliberately not supported here — the
    extensions catalogue is server-side trust; we want it baked into the
    image or a mounted volume, not pulled from the network.
    """
    raw = _read_source(source)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtensionsConfigError(f"extensions config at {source} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ExtensionsConfigError(f"extensions config at {source} must be a JSON object at the top level")

    # Allow a $schema reference for IDE autocompletion — strip before Pydantic sees it.
    payload.pop("$schema", None)

    try:
        return ExtensionsConfig.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — surface the chain with context
        raise ExtensionsConfigError(f"extensions config at {source} failed validation: {exc}") from exc


def _read_source(source: str) -> str:
    """Resolve `file://` / bare path / relative path to a UTF-8 string."""
    parsed = urlparse(source)
    if parsed.scheme == "file":
        return Path(_file_url_to_path(source)).read_text(encoding="utf-8")
    # Bare path. `urlparse("C:\\path")` reads "C" as the scheme on Windows —
    # treat any single-character scheme as a drive letter and fall through
    # to the local-file path. Absolute paths can also slip through as
    # "no scheme" on POSIX.
    if not parsed.scheme or (len(parsed.scheme) == 1 and parsed.scheme.isalpha()):
        return Path(source).read_text(encoding="utf-8")
    raise ExtensionsConfigError(
        f"extensions config source {source!r} uses unsupported scheme {parsed.scheme!r} "
        "(only file:// and bare paths are accepted)"
    )


def _file_url_to_path(url: str) -> str:
    """Cross-platform file:// to filesystem path. Mirrors openapi_loader._file_url_to_path."""
    from_uri = getattr(Path, "from_uri", None)
    if from_uri is not None:
        try:
            return str(from_uri(url))
        except (ValueError, OSError):
            pass
    parsed = urlparse(url)
    raw_path = parsed.path
    if parsed.netloc and not re.match(r"^[a-zA-Z]:$", parsed.netloc) and parsed.netloc != "localhost":
        return r"\\" + parsed.netloc + url2pathname(raw_path)
    if parsed.netloc:
        raw_path = "/" + parsed.netloc + raw_path
    return url2pathname(unquote(raw_path))


def python_annotation_for(arg: PromptArgumentConfig) -> Any:
    """Map a prompt-argument type to a runtime Python annotation."""
    return PYTHON_TYPE_MAP[arg.type]
