"""Config-driven MCP extensions — prompts, resources, and bulk-export tasks.

This package layers operator-defined components on top of the spec-driven
tools that come straight out of Shlink's OpenAPI. The shape of every
extension lives in `extensions.json` (validated by `config.py`); the
loader walks the validated config and registers each entry through the
matching FastMCP factory.

Public entry point: `load_extensions(mcp, settings, client)` in `loader.py`.
"""

from .config import ExtensionsConfig
from .loader import load_extensions

__all__ = ["ExtensionsConfig", "load_extensions"]
