# Shlink MCP Server

OAuth-gated remote MCP bridge for self-hosted Shlink, built on the shared
**bg-mcpcore** framework. See the [repository README](../../README.md) and
[docs/](../../docs/) for installation, authentication setup, and
client-connection instructions.

## Internal Architecture

This server is almost entirely declarative. The cross-cutting infrastructure —
the five inbound auth providers (Entra single/multi, Google, generic OIDC,
none), encrypted OAuth-state storage, rate limiting, structured logging, the
OpenAPI→tools source, the HTTP client + retry, and the `/healthz` · `/` ·
`/logo.svg` routes — lives in **bg-mcpcore** (a pinned GitHub dependency), tested
once there. This repo keeps only the Shlink-specific seams:

```text
src/
  config.py            Settings(BaseMcpSettings): the shlink_* backend +
                       Entra/Google credential fields + per-mode auth validation
  server.py            the bulk-export task (the profile's `python` tool source)
  main.py              make_cli() entrypoint (serve)
  profiles/
    shlink.json        declarative profile: backend, the static X-Api-Key
                       outbound resolver, the OpenAPI tool source (route maps /
                       name + description overrides / annotations), the prompt +
                       resource extensions, and the python export source
  static/
    index.html         Landing page served at /
    logo.svg           Consent-screen brand asset served at /logo.svg
extensions/
  extensions.json      operator-defined prompts + resources (loaded by bg-mcpcore)
```

Inbound auth is env-driven (`AUTH_MODE`) and resolved by bg-mcpcore's built-in
providers — there is no auth code in this repo.

Two trust boundaries that never mix:

- **Inbound** (AI client -> MCP) - OAuth 2.1 + PKCE via the configured IdP
- **Outbound** (MCP -> Shlink) - static `X-Api-Key` header

The OIDC token is never forwarded to Shlink; the static API key is never
exposed to callers.

## Build

```bash
docker build --target production -t bg-shlink-mcp . # production only
docker build --target test -t bg-shlink-mcp-test .  # run tests
docker build -t bg-shlink-mcp .                     # full pipeline (test gates production)
```

## Test

```bash
pip install ".[test]"
pytest -v
```
