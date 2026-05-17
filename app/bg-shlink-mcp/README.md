# Shlink MCP Server

OIDC-gated remote MCP bridge for self-hosted Shlink. See the
[repository README](../../README.md) and
[docs/](../../docs/) for installation, authentication setup, and
client-connection instructions.

## Internal Architecture

```
src/
  config.py              Pydantic-Settings model + AUTH_MODE validation
  logging_setup.py       structlog + Rich (console / json modes)
  server.py              FastMCP construction + lifespan
  main.py                Typer CLI (serve / tools / health)
  auth/
    provider_factory.py  AUTH_MODE -> concrete provider
    entra.py             Microsoft Entra (single + multi tenant)
    google.py            Google Workspace
    generic_oidc.py      Authentik / Keycloak / Zitadel / Auth0 / Okta / ...
  shlink/
    client.py            Async httpx wrapper (X-Api-Key, retries, error mapping)
    errors.py            Typed exception hierarchy mapped from problem+json
    openapi_loader.py    Cached spec fetch + background refresh
    tool_mapper.py       FastMCP.from_openapi() route maps + name overrides
```

Two trust boundaries that never mix:

- **Inbound** (AI client -> MCP) - OAuth 2.1 + PKCE via the configured IdP
- **Outbound** (MCP -> Shlink) - static `X-Api-Key` header

The OIDC token is never forwarded to Shlink; the static API key is never
exposed to callers.

## Build

```bash
docker build --target prod -t bg-shlink-mcp .       # production only
docker build --target test -t bg-shlink-mcp-test .  # run tests
docker build -t bg-shlink-mcp .                     # full pipeline (test gates prod)
```

## Test

```
pip install ".[test]"
pytest -v
```
