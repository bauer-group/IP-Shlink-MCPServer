#!/usr/bin/env python3
"""
bundle-openapi.py — inline external $refs in a modular OpenAPI spec.

Shlink ships its OpenAPI spec as one swagger.json index + a tree of
paths/*.json fragments tied together via relative $ref pointers. FastMCP's
from_openapi() only resolves internal #/components/... refs, not external
file refs — so we dereference the whole tree into a single self-contained
JSON document at build time.

Pure stdlib on purpose: this runs in the Dockerfile spec stage and we
don't want a pip install just for jsonref/prance.

Usage:  python3 bundle-openapi.py <input-swagger.json> <output-bundled.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def resolve(node: Any, base_dir: Path, seen: tuple[Path, ...] = ()) -> Any:
    """Walk the spec and inline every external file $ref.

    `seen` tracks the resolution stack so a malformed spec with circular
    file refs raises rather than recurses forever. Internal #/... refs
    are passed through untouched — FastMCP resolves those itself.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            ref_path, _, fragment = ref.partition("#")
            target = (base_dir / ref_path).resolve()
            if target in seen:
                raise RuntimeError(f"Circular $ref chain: {' -> '.join(str(p) for p in (*seen, target))}")
            sub = json.loads(target.read_text(encoding="utf-8"))
            if fragment:
                for part in fragment.lstrip("/").split("/"):
                    sub = sub[part]
            return resolve(sub, target.parent, seen + (target,))
        return {k: resolve(v, base_dir, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(item, base_dir, seen) for item in node]
    return node


def count_operations(spec: dict[str, Any]) -> int:
    paths = spec.get("paths", {}) or {}
    return sum(
        1
        for item in paths.values()
        if isinstance(item, dict)
        for method in item
        if method.lower() in HTTP_METHODS
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    src = Path(sys.argv[1]).resolve()
    dst = Path(sys.argv[2]).resolve()

    if not src.is_file():
        print(f"error: input spec not found: {src}", file=sys.stderr)
        return 1

    raw = json.loads(src.read_text(encoding="utf-8"))
    bundled = resolve(raw, src.parent)

    ops = count_operations(bundled)
    if ops == 0:
        print(
            f"error: bundled spec has 0 operations — input was likely already "
            f"broken or $refs failed to resolve. Source: {src}",
            file=sys.stderr,
        )
        return 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(bundled, indent=2), encoding="utf-8")
    print(f"bundled: {dst} ({dst.stat().st_size:,} bytes, {ops} operations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
