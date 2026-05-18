# bg-shlink-mcp — BAUER GROUP Shlink MCP Server

> **OIDC-gated remote MCP bridge** that exposes your self-hosted [Shlink](https://shlink.io)
> instance to AI clients — Claude Web Connectors, Claude Desktop, Microsoft 365
> Copilot, ChatGPT Connectors, Cursor, Continue.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker Image](https://img.shields.io/badge/ghcr.io-bg--shlink--mcp-blue?logo=docker)](https://github.com/bauer-group/IP-Shlink-MCPServer/pkgs/container/ip-shlink-mcpserver%2Fbg-shlink-mcp)
[![FastMCP](https://img.shields.io/badge/built%20with-FastMCP-purple)](https://gofastmcp.com)

---

## Highlights

| Feature | What it means |
| --- | --- |
| **AI-ready** | Auto-derives its tool surface from Shlink's OpenAPI spec — every endpoint of Shlink REST v3 is callable from any MCP-aware AI assistant |
| **Identity-gated** | OAuth 2.1 + PKCE on the inbound side via Entra ID (single/multi-tenant), Google Workspace, or any generic OIDC provider (Authentik, Keycloak, Zitadel, Auth0, Okta, …) |
| **Server-to-server** | Single configured Shlink API key on the outbound side — the OIDC token is *never* forwarded to Shlink |
| **Multi-arch** | `linux/amd64` and `linux/arm64` images on GHCR |
| **Three deploy flavours** | Local development, self-hosted Traefik, Coolify-managed — same image, same source |
| **Test-gated builds** | The Docker multi-stage build fails if pytest fails (no green push when tests are red) |
| **Pinned tool surface** | Shlink's OpenAPI spec is baked into the image at build time — no startup HTTP fetch, deterministic per image tag; override `SHLINK_OPENAPI_URL` at runtime to track a live source |
| **Restart-safe sessions** | Redis-backed OAuth client store with Fernet at-rest encryption — redeploys don't log users out |

---

## Architecture

```
                              AI Clients
                ┌────────────┬───────────────┬────────────┐
                │ Claude Web │ Claude Desktop│ MS Copilot │ ...
                └─────┬──────┴───────┬───────┴──────┬─────┘
                      │              │              │
                      └──────────────┼──────────────┘
                                     │ HTTPS (OAuth 2.1 + PKCE,
                                     │        Streamable HTTP /mcp)
                                     ▼
                         ┌───────────────────────┐
                         │   Traefik / Coolify   │
                         └──────────┬────────────┘
                                    │
                                    ▼
        ┌──────────────────────────────────────────────────────────┐
        │   bg-shlink-mcp container  (FastMCP, port 8000)          │
        │   ┌────────────────────────────────────────────────────┐ │
        │   │  Inbound auth (RFC 9728 / OAuth 2.1, PKCE)         │ │
        │   │   /.well-known/oauth-protected-resource            │ │
        │   │   /authorize • /token • /register                   │ │
        │   └────────────────────────────────────────────────────┘ │
        │   ┌────────────────────────────────────────────────────┐ │
        │   │  Tools auto-generated from Shlink OpenAPI 3 spec    │ │
        │   │   create_short_url, list_short_urls, get_visits, … │ │
        │   └────────────────────────────────────────────────────┘ │
        │   ┌────────────────────────────────────────────────────┐ │
        │   │  Outbound httpx client  (X-Api-Key header)          │ │
        │   └─────────────────────────┬──────────────────────────┘ │
        └────────────────────────────┼─────────────────────────────┘
                                     │ HTTP (private network)
                                     ▼
                        ┌────────────────────────┐
                        │  Shlink REST API       │
                        │  (your existing stack) │
                        └────────────────────────┘
```

Two trust boundaries that never mix:

- **Inbound** (AI client → MCP) — OAuth 2.1 + PKCE via your IdP
- **Outbound** (MCP → Shlink) — static `X-Api-Key` header

The OIDC token authenticates *who* may call a tool. The API key authenticates
*the MCP server itself* to Shlink. They are wired separately by design — a
leaked Shlink key cannot be used through the MCP without first passing OIDC.

---

## Quick Start

### 1 · Generate environment file

```bash
python scripts/generate-env.py
```

Then edit `.env` to fill:
- `SHLINK_URL` and `SHLINK_API_KEY` (`docker exec shlink shlink api-key:generate --name=mcp-server`)
- `AUTH_MODE` + the matching provider block (see [docs/authentication.md](docs/authentication.md))
- `PUBLIC_BASE_URL` to match the hostname you'll expose

### 2 · Pick a deployment flavour

```bash
# Local development (LOG_FORMAT=console, AUTH_MODE=none allowed)
docker compose -f docker-compose.development.yml up -d

# Self-hosted Traefik (HTTPS, Let's Encrypt)
docker compose -f docker-compose.traefik.yml up -d

# Coolify — paste env into the dashboard, deploy from this compose file
docker compose -f docker-compose.coolify.yml up -d
```

### 3 · Connect from a client

See [docs/client-setup.md](docs/client-setup.md). Short version:

| Client | URL |
| --- | --- |
| Claude Web | Settings → Connectors → Add custom → `https://your-host/mcp` |
| Claude Desktop | Add to `mcp.json`: `{"command": "npx", "args": ["mcp-remote", "https://your-host/mcp"]}` |
| Microsoft 365 Copilot Studio | Custom connector → MCP → `https://your-host/mcp` |
| Cursor / Continue | Add to `mcp.json` with the same URL |

---

## Repository Layout

```
IP-Shlink-MCPServer/
├── README.md                        ← you are here
├── LICENSE                          ← MIT
├── .env.example                     ← canonical config surface
├── docker-compose.development.yml   ← local dev
├── docker-compose.traefik.yml       ← self-hosted production
├── docker-compose.coolify.yml       ← Coolify deployment
│
├── scripts/
│   ├── generate-env.py              ← cross-platform .env secret generator
│   └── dev-inspector.py             ← one-command MCP Inspector launcher
│
├── app/
│   └── bg-shlink-mcp/               ← the only image this repo builds
│       ├── Dockerfile               ← multi-stage, test-gated
│       ├── pyproject.toml           ← PEP 621 deps (no requirements.txt)
│       ├── README.md                ← internal architecture
│       ├── src/                     ← Python package (PYTHONPATH=/app/src)
│       │   ├── main.py              ← Typer CLI (serve / tools / health)
│       │   ├── config.py            ← Pydantic Settings + AUTH_MODE validation
│       │   ├── server.py            ← FastMCP construction + lifespan
│       │   ├── logging_setup.py     ← structlog + Rich
│       │   ├── auth/
│       │   │   ├── provider_factory.py
│       │   │   ├── entra.py
│       │   │   ├── google.py
│       │   │   └── generic_oidc.py
│       │   ├── shlink/
│       │   │   ├── client.py
│       │   │   ├── errors.py
│       │   │   ├── openapi_loader.py
│       │   │   └── tool_mapper.py
│       │   └── static/
│       │       └── index.html       ← landing page served at /
│       └── tests/                   ← pytest (53 tests, gates Docker build)
│
├── docs/
│   ├── SHLINK-MCP-SPEC.md           ← source spec
│   ├── installation.md
│   ├── authentication.md
│   ├── client-setup.md
│   ├── testing.md                   ← Inspector workflows (local + remote)
│   ├── tools.md                     ← CI-generated tool catalogue
│   └── troubleshooting.md
│
└── .github/
    ├── dependabot.yml
    ├── config/
    └── workflows/
        ├── docker-release.yml       ← semantic-release + build/push
        ├── docker-maintenance.yml   ← auto-merge Dependabot
        ├── check-base-images.yml
        ├── ai-issue-summary.yml
        └── teams-notifications.yml
```

---

## Supported Versions

| Component | Tested | Notes |
| --- | --- | --- |
| Python | 3.13 / 3.14 | Container ships 3.14 Alpine |
| Shlink | v3.x / v4.x / v5.x | OpenAPI spec baked in at build time (default `v5.0.1`, pin via `SHLINK_OPENAPI_VERSION` build-arg) |
| FastMCP | ≥ 2.13 | `OIDCProxy`, `AzureProvider`, `GoogleProvider` |
| Redis | 7.x / 8.x | OAuth client storage (production) |

---

## Documentation

- [docs/installation.md](docs/installation.md) — step-by-step deploy for each flavour
- [docs/authentication.md](docs/authentication.md) — full walkthroughs for Entra (single/multi), Google, generic OIDC
- [docs/client-setup.md](docs/client-setup.md) — adding the connector in each AI client
- [docs/testing.md](docs/testing.md) — local & remote testing with MCP Inspector (GUI + CLI)
- [docs/tools.md](docs/tools.md) — auto-generated catalogue of MCP tools
- [docs/mcp-primitives.md](docs/mcp-primitives.md) — implementation guide for all 11 MCP Inspector tabs (Tools, Resources, Prompts, Tasks, Apps, Sampling, Elicitations, Roots, Auth, Metadata, Ping)
- [docs/troubleshooting.md](docs/troubleshooting.md) — common errors & fixes
- [docs/SHLINK-MCP-SPEC.md](docs/SHLINK-MCP-SPEC.md) — design specification (source of truth)

---

## License

MIT — see [LICENSE](LICENSE).

---

*Maintained by [BAUER GROUP](https://bauer-group.com) · Today, Tomorrow, Together*
