"""The served landing page must not ship a raw `$name` to the browser.

bg-mcpcore renders ``src/static/index.html`` with
``string.Template.safe_substitute``, which leaves an unknown placeholder in the
output instead of raising. A variable the framework does not supply therefore
fails silently and visibly: the page still renders, the card just reads
"$whatever".

The sibling server (IP-ZAMMAD-MCPServer) shipped exactly that. Its page kept a
`$zammad_url` that its own index route used to substitute; when rendering moved
into bg-mcpcore — which supplies five variables and no seam for a sixth — the
placeholder reached the browser verbatim and no test noticed. This page uses
only the framework's own variables, so the test passes today. It exists to keep
it that way.

Asserting against the SERVED html rather than a hardcoded list of expected
names is deliberate: if the framework's variable set ever changes, a name list
would go stale without failing.

Two seams worth explaining:

* The profile's tool source is swapped for the registry. The real one is
  ``openapi``, and its spec is cloned from Shlink upstream during the Docker
  build (see the Dockerfile and ``scripts/bundle-openapi.py``) — it is not
  present in a plain checkout, so building it here would mean a network call.
  The landing page does not depend on the tool surface.
* Everything the page DOES depend on stays real: the profile, the Settings
  subclass, the route registration and ``static_dir``.
"""

from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

import pytest
from bg_mcpcore import build_app_from_profile, load_profile

from config import Settings

_APP_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = _APP_ROOT / "src" / "profiles" / "shlink.json"
STATIC_DIR = _APP_ROOT / "src" / "static"


def _profile_without_openapi() -> Any:
    """The real profile, with the spec-fetching tool source swapped out."""
    raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    raw["tools"] = [{"source": "registry", "include": ["bg.ping"]}]
    return load_profile(raw)


@pytest.fixture
async def shlink_app(base_none_env) -> Any:  # type: ignore[no-untyped-def]
    """The server assembled the way main.py does, minus the OpenAPI spec."""
    return await build_app_from_profile(
        _profile_without_openapi(),
        Settings(),
        version="test",
        # main.py passes static_dir — that is what registers / and /logo.svg.
        static_dir=str(STATIC_DIR),
    )


async def test_landing_page_resolves_every_placeholder(shlink_app) -> None:  # type: ignore[no-untyped-def]
    from starlette.testclient import TestClient

    with TestClient(shlink_app.http_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    unresolved = string.Template(response.text).get_identifiers()
    assert not unresolved, f"landing page shipped unresolved placeholders: {unresolved}"


async def test_logo_is_served(shlink_app) -> None:  # type: ignore[no-untyped-def]
    """The OAuth consent screen fetches the icon from this origin."""
    from starlette.testclient import TestClient

    with TestClient(shlink_app.http_app()) as client:
        response = client.get("/logo.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
