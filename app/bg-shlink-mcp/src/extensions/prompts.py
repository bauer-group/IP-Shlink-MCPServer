"""Register operator-defined prompts from `extensions.json`.

A prompt entry becomes a FastMCP `FunctionPrompt` whose body is just
`string.Template(...).substitute(**kwargs)`. The trickier part is the
**function signature** — FastMCP derives each prompt's argument schema
from `inspect.signature(fn)`, so we synthesise a real signature on a
generic closure at registration time. No `exec()`, no source generation;
just `inspect.Signature` plus `__annotations__`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from string import Template
from typing import Any

import structlog
from fastmcp import FastMCP

from .config import PromptConfig, python_annotation_for

logger = structlog.stdlib.get_logger("bg-shlink-mcp.extensions.prompts")


def register_prompts(mcp: FastMCP, prompts: list[PromptConfig]) -> int:
    """Register every prompt entry on the given FastMCP instance.

    Returns the number of prompts successfully registered. A failure on
    one prompt does NOT abort the others — we log and continue, so an
    operator typo in a single config entry doesn't take the whole server
    down at boot.
    """
    registered = 0
    for cfg in prompts:
        try:
            fn = _build_prompt_function(cfg)
            mcp.prompt(
                name=cfg.name,
                title=cfg.title,
                description=cfg.description or None,
                tags=set(cfg.tags) if cfg.tags else None,
            )(fn)
            registered += 1
            logger.info("extensions.prompt_registered", name=cfg.name, args=[a.name for a in cfg.arguments])
        except Exception as exc:  # noqa: BLE001 — register-many, one bad apple shouldn't block
            logger.error("extensions.prompt_registration_failed", name=cfg.name, error=str(exc))
    return registered


def _build_prompt_function(cfg: PromptConfig) -> Callable[..., str]:
    """Synthesise a callable with a real signature derived from the config.

    The body just does `Template.substitute` over the provided kwargs. The
    signature exists so FastMCP can introspect parameter names, types, and
    defaults the same way it does for hand-written `@mcp.prompt` functions.
    """
    template = Template(cfg.template)

    # `safe_substitute` swallows missing keys (returns them as `$name`); we use
    # the strict `substitute` because the config validator already guarantees
    # placeholders == declared arguments, so any KeyError here would be a bug.
    def _runner(**kwargs: Any) -> str:
        # All values pass through str() — Template requires str values.
        # FastMCP delivers them in their declared Python types (int, bool, …),
        # so int(30) becomes "30" in the rendered prompt, which is what we want.
        return template.substitute({k: str(v) for k, v in kwargs.items()})

    _runner.__name__ = cfg.name
    _runner.__doc__ = cfg.description or None

    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    for arg in cfg.arguments:
        annotation = python_annotation_for(arg)
        default: Any = inspect.Parameter.empty
        if arg.default is not None:
            default = arg.default
        elif not arg.required:
            # Optional with no explicit default — fall back to the type's zero-value.
            default = annotation()
        parameters.append(
            inspect.Parameter(
                name=arg.name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
        annotations[arg.name] = annotation

    _runner.__signature__ = inspect.Signature(parameters=parameters, return_annotation=str)  # type: ignore[attr-defined]
    _runner.__annotations__ = {**annotations, "return": str}
    return _runner
