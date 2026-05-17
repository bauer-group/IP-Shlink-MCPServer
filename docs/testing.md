# Testing

How to exercise the Shlink MCP server end-to-end — **locally** (against a
container on your laptop) or **remotely** (against a deployed Traefik /
Coolify instance) — using the **official MCP Inspector**.

> MCP Inspector is the reference debugging UI from the Model Context Protocol
> project. It speaks every transport (`stdio`, `streamable-http`,
> deprecated `sse`), drives the OAuth 2.1 + PKCE flow for you, and lets you
> invoke tools/resources/prompts interactively — exactly what you need
> before pointing Claude Web, Claude Desktop, or Copilot Studio at a fresh
> deployment.

---

## Why Inspector first

| Concern | Inspector | Real client (Claude, Copilot, …) |
| --- | --- | --- |
| Setup time | `npx` one-liner | Configure connector + auth + restart |
| OAuth diagnostics | Full request/response visible | Hidden behind the client UI |
| Tool surface | Live `tools/list` dump with schemas | Curated by the LLM |
| Reproducible failures | Yes — exact JSON-RPC payloads | Hard — LLM wording varies |
| CI/automation | Yes (`--cli` mode) | No |

If a deployment works in Inspector but not in Claude, the issue is in the
client integration. If it fails in Inspector, the issue is in the MCP
server itself — much easier to triage.

### Tool landscape — pick the right one

Inspector isn't the only MCP testing tool. It earns its "primary" status
because it's the **only one that exercises the full OAuth-gated remote
path interactively**. The others are complementary, not replacements:

| Tool | Best for | Limitations |
| --- | --- | --- |
| **MCP Inspector** (this doc) | End-to-end inc. OAuth/PKCE, HTTP sessions, ad-hoc tool calls | Browser-dependent, awkward in headless CI |
| **`fastmcp dev`** (Python sugar) | Quick stdio loop against a local FastMCP app | *Wraps Inspector* — stdio only, no OAuth, no HTTP |
| **`mcp dev`** (official SDK sugar) | Same as `fastmcp dev` for servers built on `mcp.server` | *Wraps Inspector* — does **not** work with FastMCP 2.x apps |
| **`curl` / `httpie`** | Sanity-checking `/healthz`, well-known endpoints, 401 behaviour | Cannot carry streamable-http `Mcp-Session-Id` state, cannot do PKCE-in-a-browser |
| **`pytest` + FastMCP `Client`** | Unit tests of tool logic, error mapping, schemas | No real network, no real Shlink, no real OAuth |

**The "is Inspector redundant?" question.** `mcp dev` and `fastmcp dev`
look like alternatives but actually spawn `@modelcontextprotocol/inspector`
under the hood — they're npm-script convenience wrappers, not separate
test frameworks. The only true alternative to Inspector would be a
custom JSON-RPC client written from scratch, which trades convenience for
no real gain on this project. Use the wrappers when they help; reach for
plain `npx` when you need transport/host control they don't expose.

---

## Prerequisites

- **Node.js ≥ 22.7.5** (Inspector is a Node CLI, shipped via `npx`).
  Check with `node -v` — older versions fail with a cryptic
  `ERR_PACKAGE_PATH_NOT_EXPORTED`.
- A reachable MCP endpoint. Pick one of:
  - **Local:** `docker compose -f docker-compose.development.yml up -d`
    (defaults to `http://localhost:8000/mcp`, `AUTH_MODE=none`)
  - **Remote:** any deployed flavour (`https://<your-host>/mcp`)
- For remote tests against a Traefik/Coolify deployment: a valid IdP
  account that the configured `AUTH_MODE` accepts.

---

## Launching Inspector

The canonical invocation is a single command — no install step:

```bash
npx @modelcontextprotocol/inspector
```

What happens:

1. `npx` fetches the latest `@modelcontextprotocol/inspector` package.
2. It boots two local processes:
   - **MCP Inspector Proxy** on `http://127.0.0.1:6277` — speaks every MCP
     transport, browsers can't.
   - **MCP Inspector Client** (GUI) on `http://127.0.0.1:6274`.
3. Your browser is opened to the GUI automatically. A session token is
   printed to the terminal — the GUI link in stdout already includes it as
   `?MCP_PROXY_AUTH_TOKEN=...`, so click that link rather than typing
   `localhost:6274` by hand.

Keep the terminal open — closing it kills the proxy.

> **Note:** if port `6274` or `6277` is already in use (another Inspector
> session, dev server, …), set `CLIENT_PORT` / `SERVER_PORT` env vars:
> `CLIENT_PORT=6300 SERVER_PORT=6301 npx @modelcontextprotocol/inspector`.

---

## Scenario 1 · Local server, no auth (fastest smoke test)

The development compose ships with `AUTH_MODE=none` permitted — the cleanest
target for a first Inspector run.

### 1.1 · Bring the stack up

```bash
# Generate .env if you haven't
python scripts/generate-env.py

# Fill in SHLINK_URL + SHLINK_API_KEY, leave AUTH_MODE=none

docker compose -f docker-compose.development.yml up -d --build
docker compose -f docker-compose.development.yml logs -f shlink-mcp
```

Verify the container is healthy:

```bash
curl -fsS http://localhost:8000/healthz
# → {"status":"ok"}
```

### 1.2 · Point Inspector at it

In the Inspector GUI:

| Field | Value |
| --- | --- |
| Transport Type | `Streamable HTTP` |
| URL | `http://localhost:8000/mcp` |
| Authentication | *(leave blank — `AUTH_MODE=none`)* |

Click **Connect**. The right panel should flip to "Connected" and the left
sidebar lists the auto-generated tools (`create_short_url`,
`list_short_urls`, `get_visits`, …).

### 1.3 · Exercise the tool surface

In Inspector:

1. **Tools tab** → pick `list_short_urls` → set `itemsPerPage` to `5` →
   **Run Tool**. The response panel should show a JSON array of your most
   recent short URLs.
2. **Tools tab** → `create_short_url` → set `longUrl` to e.g.
   `https://example.com/test` → **Run Tool**. Confirm a new short code is
   returned.
3. **Resources tab** (if any are exposed) → click each entry to verify
   `resources/read` works.

If `list_short_urls` succeeds, the **outbound** path (MCP → Shlink) is
healthy. Onwards.

---

## Scenario 2 · Local server, stdio transport (no HTTP at all)

If you suspect the HTTP / Streamable-HTTP layer is the problem — or just
want the leanest possible loop — Inspector can spawn the server directly
over `stdio`. No proxy, no port, no OAuth.

### 2.1 · Run against the running container

```bash
npx @modelcontextprotocol/inspector \
  docker exec -i bg-shlink-mcp \
  python src/main.py serve --transport stdio
```

Inspector spawns the command, pipes its stdin/stdout into the proxy, and
the GUI connects as if it were a `Streamable HTTP` endpoint. From this
point the workflow is identical to Scenario 1 — tools, resources, prompts
all show up.

### 2.2 · Run against a local source checkout (no Docker)

If you're hacking on `app/bg-shlink-mcp/src/` and want hot iteration
without rebuilding the image:

```bash
cd app/bg-shlink-mcp
pip install -e .

# .env values still apply — set them in the shell or via direnv
export SHLINK_URL=https://shlink.example.com
export SHLINK_API_KEY=...
export AUTH_MODE=none
export ENVIRONMENT=development

npx @modelcontextprotocol/inspector \
  python src/main.py serve --transport stdio
```

Edit a Python file → kill Inspector with `Ctrl+C` → rerun → reconnect.
No Docker rebuild in the loop.

---

## Scenario 3 · Remote server, OAuth-gated (the production-fidelity test)

This is the **acceptance test before you point real clients** at a fresh
Traefik or Coolify deployment.

### 3.1 · Preflight checks

```bash
HOST=https://your-host

# Health
curl -fsS $HOST/healthz                                   # → {"status":"ok"}

# RFC 9728 protected-resource metadata
curl -fsS $HOST/.well-known/oauth-protected-resource | jq .

# OAuth-server metadata (forwarded by FastMCP from the upstream IdP)
curl -fsS $HOST/.well-known/oauth-authorization-server | jq .

# /mcp must gate correctly
curl -i -X POST $HOST/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# Expect: HTTP/1.1 401 + WWW-Authenticate: Bearer resource_metadata="..."
```

If any of the four fail, fix the deployment before reaching for Inspector —
see [installation.md §3](installation.md) and [troubleshooting.md](troubleshooting.md).

### 3.2 · Connect Inspector

In the Inspector GUI:

| Field | Value |
| --- | --- |
| Transport Type | `Streamable HTTP` |
| URL | `https://your-host/mcp` |
| Authentication | *(leave blank — Inspector discovers the IdP automatically)* |

Click **Connect**. Three things happen, in order:

1. Inspector POSTs to `/mcp` and receives `401` + the `WWW-Authenticate`
   header with `resource_metadata=...`.
2. Inspector fetches `/.well-known/oauth-protected-resource`, then the
   referenced `/.well-known/oauth-authorization-server`. It discovers the
   `/authorize` and `/token` endpoints, the supported scopes, and that
   **Dynamic Client Registration** is available.
3. Inspector dynamically registers itself via `/register` (DCR), then opens
   a browser tab pointing at `/authorize`. You sign in via your IdP
   (Entra / Google / Authentik / …), consent, and are redirected back. The
   browser closes and Inspector flips to "Connected".

The full handshake is logged in the Inspector terminal — handy when
diagnosing a stuck flow.

### 3.3 · What to verify after connecting

- The **tool count** matches what `python src/main.py tools` produces in
  the container (the catalogue is auto-derived from Shlink's OpenAPI).
- A **read-only call** (`list_short_urls`, `get_visits`) succeeds.
- A **write call** (`create_short_url`) succeeds *and* the new code is
  visible in the Shlink web UI — confirms both directions of the data
  pipeline.
- The Inspector **History tab** shows the request/response JSON for each
  call — sanity check there are no `error` envelopes.

---

## Scenario 4 · Headless / CI testing (`--cli` mode)

Inspector also has a non-interactive CLI for scripts and pipelines. Useful
for smoke-testing a deployment from a GitHub Actions workflow without a
browser.

```bash
# List tools (works against AUTH_MODE=none only — CLI mode can't do OAuth)
npx @modelcontextprotocol/inspector \
  --cli http://localhost:8000/mcp \
  --method tools/list

# Call a specific tool
npx @modelcontextprotocol/inspector \
  --cli http://localhost:8000/mcp \
  --method tools/call \
  --tool-name list_short_urls \
  --tool-arg itemsPerPage=5
```

For OAuth-gated remote servers, either:

- Run Inspector in GUI mode once to obtain a bearer token, paste it into a
  CI secret, then pass it via `--header "Authorization: Bearer <token>"`,
  *or*
- Test the unauthenticated surface only (`/healthz`,
  `/.well-known/oauth-protected-resource`) with plain `curl` and reserve
  the full MCP roundtrip for staging.

---

## Companion tools (no Inspector required)

When Inspector is overkill — or when you want a deterministic loop without
a browser in the way — these are the alternatives.

### `fastmcp dev` — leanest stdio loop

If you're hacking on tool descriptions or schemas and only need to see
*"do the tools still parse"*, the FastMCP CLI is shorter than the manual
`npx` invocation from Scenario 2:

```bash
cd app/bg-shlink-mcp
fastmcp dev src/main.py
```

`fastmcp dev` discovers the FastMCP app object automatically, sets
`PYTHONPATH`, and launches `@modelcontextprotocol/inspector` against the
spawned process. It's literally `npx @modelcontextprotocol/inspector …`
under the hood — same GUI, fewer keystrokes. **It does not test the
HTTP transport or OAuth** — for those, use Scenarios 1 or 3.

> Don't confuse with `mcp dev` from the official `pip install mcp` SDK —
> that one only works with servers built on `mcp.server.Server`, not
> FastMCP 2.x.

### In-container CLI subcommands

The container ships three Typer subcommands —
see [main.py:106-156](../app/bg-shlink-mcp/src/main.py#L106-L156):

```bash
# Live tool catalogue — same content as docs/tools.md
docker exec bg-shlink-mcp python src/main.py tools

# Shlink reachability probe — exits 0 (healthy) or 1
docker exec bg-shlink-mcp python src/main.py health

# Run the pytest suite (53 tests, also gates the Docker build)
docker exec bg-shlink-mcp pytest -q
```

### `pytest` with FastMCP's in-memory `Client`

For unit-testing tool logic without any process, network, or Inspector:

```python
import pytest
from fastmcp import Client
from server import build_app
from config import Settings

@pytest.mark.asyncio
async def test_list_short_urls_returns_results():
    settings = Settings(...)              # test fixture; see tests/conftest.py
    mcp = await build_app(settings)

    # Client(<server>) opens an in-memory transport — no sockets, no JSON wire
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_short_urls",
            {"itemsPerPage": 5},
        )
        assert "shortUrls" in result.data
```

The Shlink HTTP client should be patched with `respx` or `pytest-httpx` —
see the existing patterns under [tests/](../app/bg-shlink-mcp/tests/).

### Direct JSON-RPC with `curl` / `httpie`

Useful for verifying the **unauthenticated surface** of a deployment from
a shell — the very same probes used by the post-deploy validation in
[installation.md §3](installation.md#3--validation-checklist):

```bash
HOST=https://your-host

# Liveness
curl -fsS $HOST/healthz

# Protected-resource metadata (RFC 9728)
curl -fsS $HOST/.well-known/oauth-protected-resource | jq .

# OAuth-server metadata (forwarded from the upstream IdP)
curl -fsS $HOST/.well-known/oauth-authorization-server | jq .

# Confirm /mcp gates correctly (expect 401 + WWW-Authenticate)
curl -i -X POST $HOST/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

**Don't try to drive a full MCP session with `curl`.** Streamable-HTTP
hands out a `Mcp-Session-Id` header after `initialize` that every
subsequent request must echo back, and the OAuth `code → token` exchange
requires a real browser for PKCE. Either of those is where Inspector
earns its place.

---

## Common failure modes

### Inspector shows "Connected" but `tools/list` returns 0 tools

The Shlink OpenAPI fetch failed at startup. Tools are derived from the
spec — no spec, no tools. Check:

```bash
docker compose -f docker-compose.development.yml logs shlink-mcp | grep openapi
```

Common culprits: `SHLINK_URL` typo, Shlink container not on the same
network, or the pinned spec URL (`SHLINK_OPENAPI_URL`) is unreachable.

### Inspector hangs at "Authenticating…" against a remote server

Three likely causes:

1. **Redirect URI mismatch.** The IdP rejects the callback. Open browser
   devtools → Network tab → look for the `400` from the IdP and copy the
   `redirect_uri` query param. It must exactly match what's registered
   ([troubleshooting.md §OAuth flow](troubleshooting.md#oauth-flow)).
2. **DCR refused by the IdP.** Some Entra tenants disable Dynamic Client
   Registration. Symptom: `/register` returns `403` in the Inspector log.
   Switch to a provider that supports DCR (Authentik, Keycloak, Zitadel),
   or pre-register Inspector as a known client.
3. **`PUBLIC_BASE_URL` mismatch.** FastMCP uses it verbatim in the
   `resource_metadata` URL and the OAuth metadata. If the value in `.env`
   doesn't match what users actually type, the loop never closes.

### Inspector says "Failed to spawn process" in stdio mode

The command you passed to `npx @modelcontextprotocol/inspector …` isn't
on the PATH **of the Inspector terminal**, or the container name in
`docker exec …` is wrong. Run it standalone first:

```bash
docker exec -i bg-shlink-mcp python src/main.py serve --transport stdio
# Should print a JSON-RPC initialization line to stdout
```

### `401 Unauthorized` on every Inspector call after a fresh deployment

Your `AUTH_JWT_SIGNING_KEY` or `AUTH_STORAGE_ENCRYPTION_KEY` was
regenerated between Inspector launches. Inspector cached the old token;
the server can't verify it. Disconnect → reconnect in Inspector to force
a fresh OAuth round trip.

---

## Quick reference

| Goal | Command |
| --- | --- |
| Inspector GUI, paste URL by hand | `npx @modelcontextprotocol/inspector` |
| Inspector against running container (stdio) | `npx @modelcontextprotocol/inspector docker exec -i bg-shlink-mcp python src/main.py serve --transport stdio` |
| Inspector against local source checkout (stdio) | `npx @modelcontextprotocol/inspector python src/main.py serve --transport stdio` |
| Same as above, FastMCP sugar | `fastmcp dev src/main.py` |
| Headless CI smoke test | `npx @modelcontextprotocol/inspector --cli <url> --method tools/list` |
| Container tool catalogue | `docker exec bg-shlink-mcp python src/main.py tools` |
| Container Shlink probe | `docker exec bg-shlink-mcp python src/main.py health` |
| Container test suite | `docker exec bg-shlink-mcp pytest -q` |
| Unit test with in-memory transport | `from fastmcp import Client; async with Client(mcp) as c: ...` |
| Public unauth-surface sanity (curl) | `curl -fsS $HOST/.well-known/oauth-protected-resource \| jq .` |

---

## Related

- [client-setup.md](client-setup.md) — connecting real AI clients once
  Inspector has validated the deployment
- [authentication.md](authentication.md) — the OAuth flow Inspector
  exercises end-to-end
- [troubleshooting.md](troubleshooting.md) — server-side failure modes
- Inspector source & changelog: <https://github.com/modelcontextprotocol/inspector>
