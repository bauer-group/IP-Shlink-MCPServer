# Shlink MCP Server — Implementation Spec

> Source-of-truth specification for the new repository **[bauer-group/IP-Shlink-MCPServer](https://github.com/bauer-group/IP-Shlink-MCPServer)**.
> Hand this document to the developer; everything they need to start is below.

> **Naming note (v0.1.0 release):**
> Following BAUER GROUP convention, the canonical identifier was renamed
> `shlink-mcp` → **`bg-shlink-mcp`** for: directory (`app/bg-shlink-mcp/`),
> Python package, FastMCP server name, container name (`STACK_NAME` default),
> GHCR image segment, and Docker image labels. The Compose **service name**
> stays `shlink-mcp:` so existing `docker compose logs/exec` snippets keep
> working unchanged. Read every `shlink-mcp` in the body below as the modern
> `bg-shlink-mcp`.

---

## 1. Mission

Build a **remote, multi-tenant-capable MCP server** that exposes a self-hosted Shlink instance to AI clients (Claude Web Connectors, Claude Desktop, Claude Code, Microsoft 365 Copilot, ChatGPT Connectors, Cursor, Continue). The server must:

1. Run as a long-lived **HTTPS service in Docker**, ready for Traefik / Coolify front-ends.
2. Use a **single, configured Shlink API key** to talk to Shlink (server-to-server).
3. Authenticate **MCP clients** via OAuth 2.1 + PKCE through a configurable upstream IdP:
   * Microsoft Entra ID — single-tenant
   * Microsoft Entra ID — multi-tenant
   * Google Workspace
   * Any generic OIDC provider (Authentik, Keycloak, Zitadel, Auth0, Okta, …)
4. **Auto-derive its tool surface** from Shlink's OpenAPI spec — no hand-maintained tool list.
5. Be operated and shipped exactly the same way as **[bauer-group/CS-Outline](https://github.com/bauer-group/CS-Outline)** or local at "C:\Projects\Container-Solution\Outline" — three compose flavours, multi-stage Dockerfile with test-gated prod stage, GHCR image, semantic-release.

Out of scope for v1:
* Multi-Shlink-instance fan-out (one server = one Shlink). Multi-instance can be added later by deploying multiple containers behind distinct hostnames.
* User-level permission scoping inside Shlink (Shlink has no concept of per-user API keys — the configured key is the trust anchor, OIDC only gates *who may call the MCP*).
* Persistent user-state, audit log, analytics dashboards (see #13 roadmap).

---

## 2. Stack & Key Dependencies

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.14 (Alpine base) | Already established in `bauer-group/CS-Outline`, smallest cognitive load for the team |
| MCP framework | **FastMCP** (`fastmcp >= 2.13`, the [PrefectHQ/jlowin](https://github.com/jlowin/fastmcp) project) | Built-in `GoogleProvider`, `AzureProvider`, `OAuthProxy` for generic OIDC; `from_openapi()` ingests Shlink's spec |
| Transport | `streamable-http` | Current MCP wire format (SSE deprecated); single POST endpoint, plays nice with reverse proxies |
| HTTP client | `httpx` (async) | Same as Outline-backup; FastMCP uses it internally |
| Config | `pydantic-settings >= 2.14` | Type-safe env parsing, same as Outline-backup |
| Logging | `structlog` + Rich | JSON in prod, pretty in dev |
| CLI | `typer` | Same as Outline-backup; subcommands `serve` / `tools list` / `health` |
| Test | `pytest` + `pytest-httpx` | Build-gates prod stage (mirror Outline-backup) |
| Process supervisor | `tini` as PID 1 | Clean signal forwarding |

---

## 3. Architecture

```text
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
                         │       Traefik         │
                         │ (or Coolify ingress)  │
                         └──────────┬────────────┘
                                    │
                                    ▼
        ┌──────────────────────────────────────────────────────────┐
        │  shlink-mcp container  (FastMCP, port 8000)              │
        │                                                          │
        │  ┌────────────────────────────────────────────────────┐  │
        │  │  Auth Layer (FastMCP-mounted, RFC 9728 metadata)    │  │
        │  │   /.well-known/oauth-protected-resource             │  │
        │  │   /authorize • /token • /register  (via OAuthProxy) │  │
        │  │   Provider: Entra | Google | Generic OIDC           │  │
        │  └────────────────────────────────────────────────────┘  │
        │  ┌────────────────────────────────────────────────────┐  │
        │  │  MCP Tools (auto-generated from Shlink OpenAPI)     │  │
        │  │   create_short_url, list_short_urls, get_visits, … │  │
        │  └────────────────────────────────────────────────────┘  │
        │  ┌────────────────────────────────────────────────────┐  │
        │  │  ShlinkClient  (httpx, X-Api-Key header)            │  │
        │  └─────────────────────────┬──────────────────────────┘  │
        └────────────────────────────┼─────────────────────────────┘
                                     │ HTTP (private Docker net)
                                     ▼
                        ┌────────────────────────┐
                        │  Shlink REST API       │
                        │  (your existing stack) │
                        └────────────────────────┘
```

Two independent trust boundaries:

* **MCP boundary** — caller proves identity via OAuth/OIDC (user level).
* **Shlink boundary** — server proves authority via static `X-Api-Key` (service level).

The OIDC token is **not** forwarded to Shlink. The MCP server is the only thing that ever talks to Shlink; the OIDC layer only decides *whether* a tool call is allowed to proceed.

---

## 4. Repository Layout

Mirror the `bauer-group/CS-Outline` structure as required. The developer should be able to navigate this repo on day one without learning anything new.

```text
IP-Shlink-MCPServer/
├── README.md                          ← public README (Outline-style highlights table)
├── LICENSE                            ← MIT (BAUER GROUP standard)
├── .env.example                       ← canonical config surface (see #7)
├── .gitignore
├── .dockerignore
│
├── docker-compose.development.yml     ← local dev (host port 8000, no proxy)
├── docker-compose.traefik.yml         ← self-hosted Traefik (HTTPS)
├── docker-compose.coolify.yml         ← Coolify deployment
│
├── scripts/
│   └── generate-env.py                ← cross-platform .env generator (port from CS-Outline)
│
├── app/
│   └── shlink-mcp/                    ← the only image this repo builds
│       ├── Dockerfile                 ← multi-stage, python:3.14-alpine, test-gated
│       ├── pyproject.toml             ← src-layout; pinned + tested constraints
│       ├── requirements.txt           ← runtime pins (no dev deps)
│       ├── requirements-test.txt      ← pytest + httpx test fixtures
│       ├── pytest.ini
│       ├── .dockerignore
│       ├── README.md                  ← internal architecture (mirror outline-backup/README.md)
│       │
│       ├── src/                       ← Python package root  (PYTHONPATH=/app/src)
│       │   ├── __init__.py
│       │   ├── main.py                ← CLI entrypoint (typer)
│       │   ├── config.py              ← pydantic-settings model — see #7
│       │   ├── logging_setup.py       ← structlog + Rich
│       │   │
│       │   ├── auth/
│       │   │   ├── __init__.py
│       │   │   ├── provider_factory.py  ← env → FastMCP auth provider (Entra/Google/OIDC)
│       │   │   ├── entra.py             ← single + multi tenant wiring
│       │   │   ├── google.py            ← GoogleProvider wrapper
│       │   │   └── generic_oidc.py      ← OAuthProxy for Authentik/Keycloak/etc.
│       │   │
│       │   ├── shlink/
│       │   │   ├── __init__.py
│       │   │   ├── client.py            ← thin httpx wrapper (X-Api-Key, retries, errors)
│       │   │   ├── openapi_loader.py    ← fetch/refresh spec, cache, version pin
│       │   │   └── tool_mapper.py       ← FastMCP.from_openapi() + tool descriptions
│       │   │
│       │   └── server.py                ← FastMCP() construction, transport, lifespan
│       │
│       └── tests/
│           ├── __init__.py
│           ├── test_config.py            ← all 4 auth modes parse cleanly
│           ├── test_provider_factory.py  ← right provider for each AUTH_MODE
│           ├── test_shlink_client.py     ← httpx mock, header, error mapping
│           ├── test_tool_mapper.py       ← OpenAPI → MCP tool conversion
│           └── conftest.py
│
├── docs/
│   ├── installation.md                ← step-by-step (dev / Traefik / Coolify)
│   ├── authentication.md              ← Entra single & multi, Google, generic OIDC
│   ├── client-setup.md                ← how to add the connector in Claude Web / Desktop / Copilot
│   ├── tools.md                       ← generated tool catalogue (CI artifact)
│   └── troubleshooting.md
│
└── .github/
    ├── CODEOWNERS
    ├── dependabot.yml                 ← docker + pip ecosystems
    ├── config/
    │   ├── docker-base-image-monitor/base-images.json
    │   └── release/semantic-release.json
    └── workflows/
        ├── ai-issue-summary.yml
        ├── check-base-images.yml
        ├── docker-maintenance.yml
        ├── docker-release.yml         ← validate + release + build shlink-mcp
        └── teams-notifications.yml
```

> All five workflow files have direct copies in `CS-Outline` — start by copying them and rename `outline-backup` → `shlink-mcp` (see #11).

---

## 5. Tool Surface — Auto-Generated from OpenAPI

Do **not** hand-maintain a tool list. The Shlink team ships an authoritative OpenAPI 3 spec at <https://api-spec.shlink.io/> (raw file at the same URL with `Accept: application/json`). FastMCP can ingest it directly:

```python
from fastmcp import FastMCP
from fastmcp.server.openapi import RouteMap, MCPType
import httpx

async def build_server(settings: Settings) -> FastMCP:
    spec = await load_shlink_spec(settings.shlink_openapi_url)
    client = httpx.AsyncClient(
        base_url=settings.shlink_url.rstrip("/") + "/rest/v3",
        headers={"X-Api-Key": settings.shlink_api_key.get_secret_value()},
        timeout=30,
    )
    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name="shlink",
        route_maps=[
            # Health-check endpoint is exposed as a Resource, not a Tool.
            RouteMap(pattern=r"^/health$", mcp_type=MCPType.RESOURCE),
            # Mercure / event-stream endpoints are skipped entirely.
            RouteMap(pattern=r"^/mercure-info$", mcp_type=MCPType.EXCLUDE),
        ],
        tool_naming="snake_case",  # GET /short-urls → list_short_urls
    )
    return mcp
```

### Endpoint → Tool Mapping (Shlink REST v3)

Generated tools, grouped for the documentation page `docs/tools.md`:

| Category | Shlink Endpoint | Generated Tool |
| --- | --- | --- |
| **Short URLs** | `POST /short-urls` | `create_short_url` |
|  | `GET /short-urls` | `list_short_urls` |
|  | `GET /short-urls/{shortCode}` | `get_short_url` |
|  | `PATCH /short-urls/{shortCode}` | `edit_short_url` |
|  | `DELETE /short-urls/{shortCode}` | `delete_short_url` |
| **Redirect Rules** | `GET /short-urls/{shortCode}/redirect-rules` | `get_redirect_rules` |
|  | `POST /short-urls/{shortCode}/redirect-rules` | `set_redirect_rules` |
| **Visits / Analytics** | `GET /short-urls/{shortCode}/visits` | `get_short_url_visits` |
|  | `GET /tags/{tag}/visits` | `get_tag_visits` |
|  | `GET /domains/{domain}/visits` | `get_domain_visits` |
|  | `GET /visits/non-orphan` | `get_non_orphan_visits` |
|  | `GET /visits/orphan` | `get_orphan_visits` |
|  | `GET /visits` | `get_visits_summary` |
| **Tags** | `GET /tags` | `list_tags` |
|  | `GET /tags/stats` | `get_tag_stats` |
|  | `PUT /tags` | `rename_tag` |
|  | `DELETE /tags` | `delete_tags` |
| **Domains** | `GET /domains` | `list_domains` |
|  | `PATCH /domains/redirects` | `set_domain_redirects` |
| **System** | `GET /health` | (exposed as MCP Resource `shlink://health`) |

The mapping is **not** copied into source code as a static list. It lives in
`src/shlink/tool_mapper.py` as the route-map filter, plus a CI step that prints
the actual list to `docs/tools.md` on every release so the public docs stay in
sync with whatever Shlink's spec currently advertises (see #11).

### Why auto-generation matters here

Shlink ships breaking API changes between major versions (v2 → v3 dropped four
endpoints, renamed three). With hand-maintained tools, every Shlink major
becomes a multi-day port. With OpenAPI ingestion the developer just bumps
`SHLINK_OPENAPI_URL` (or the pinned spec file), reruns the test suite, and
ships.

---

## 6. Authentication Model

### 6.1 Two layers, never confused

| Layer | Direction | Mechanism | Configured via |
| --- | --- | --- | --- |
| **MCP ↔ AI client** | inbound | OAuth 2.1 + PKCE via upstream IdP | `AUTH_MODE` + provider env block |
| **MCP → Shlink** | outbound | Static API key, `X-Api-Key` header | `SHLINK_URL`, `SHLINK_API_KEY` |

The Shlink API key **never** transits to a caller and **never** depends on the
OIDC layer. It is required for the server to start (Pydantic enforces this at
boot).

### 6.2 `AUTH_MODE` selector

A single env var picks which provider FastMCP mounts:

```
AUTH_MODE=entra-single | entra-multi | google | oidc | none
```

`none` is allowed **only** when `ENVIRONMENT=development`, with a loud log
warning at startup. Pydantic must enforce this — production deployments cannot
silently slip into no-auth.

### 6.3 Entra ID — single-tenant

```python
from fastmcp.server.auth.providers.azure import AzureProvider

AzureProvider(
    client_id=settings.entra_client_id,
    client_secret=settings.entra_client_secret.get_secret_value(),
    tenant_id=settings.entra_tenant_id,   # the GUID of your tenant
    base_url=settings.public_base_url,    # e.g. https://shlink-mcp.bauer-group.com
    required_scopes=["openid", "profile", "email", "offline_access"],
)
```

Redirect URI Entra needs:
```
{PUBLIC_BASE_URL}/auth/azure/callback
```

### 6.4 Entra ID — multi-tenant

Same `AzureProvider`, but the `tenant_id` becomes the magic literal `organizations` (work/school accounts from any tenant) or `common` (work/school + personal). Set:

```
AUTH_MODE=entra-multi
ENTRA_TENANT_ID=organizations    # or 'common'
ENTRA_ALLOWED_TENANTS=tenant-guid-1,tenant-guid-2   # post-issuance check
```

The provider validates JWT signature via the multi-tenant JWKS endpoint, then the
post-issuance check rejects any token whose `tid` claim isn't on the allowlist.
This is the only way to support multi-tenant Entra without giving every Entra
user on the planet access — Microsoft will issue tokens for any tenant.

### 6.5 Google Workspace

```python
from fastmcp.server.auth.providers.google import GoogleProvider

GoogleProvider(
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret.get_secret_value(),
    base_url=settings.public_base_url,
    required_scopes=["openid", "email"],
    allowed_domains=settings.google_allowed_domains,  # comma-list of `hd` claim values
)
```

Redirect URI:
```
{PUBLIC_BASE_URL}/auth/google/callback
```

If `GOOGLE_ALLOWED_DOMAINS` is set (e.g. `bauer-group.com`), accept only tokens
whose `hd` claim matches — Google sets this for Workspace accounts. Personal
Gmail accounts have no `hd` claim and are rejected.

### 6.6 Generic OIDC (Authentik, Keycloak, Zitadel, Auth0, …)

```python
from fastmcp.server.auth.providers.oauth_proxy import OAuthProxy

OAuthProxy(
    upstream_authorization_endpoint=settings.oidc_auth_uri,
    upstream_token_endpoint=settings.oidc_token_uri,
    upstream_jwks_uri=settings.oidc_jwks_uri,
    upstream_client_id=settings.oidc_client_id,
    upstream_client_secret=settings.oidc_client_secret.get_secret_value(),
    base_url=settings.public_base_url,
    required_scopes=settings.oidc_scopes.split(),
    issuer=settings.oidc_issuer,            # validates `iss` claim
)
```

Either give `OIDC_DISCOVERY_URL` (e.g. `https://auth.example.com/.well-known/openid-configuration`) and let the loader pull endpoints, **or** set each `OIDC_*_URI` explicitly. Pydantic must accept either form but require one of them when `AUTH_MODE=oidc`.

### 6.7 Persistence

OAuth tokens and DCR client registrations must survive container restarts —
otherwise every redeploy logs every user out. Two options:

1. **JWT signing key + in-memory client store** (dev / single-replica) — set
   `AUTH_JWT_SIGNING_KEY` (32-byte hex) so issued bearer tokens stay valid
   across restarts.
2. **Redis-backed client store** (production / multi-replica) — set
   `AUTH_REDIS_URL=redis://...` and `AUTH_STORAGE_ENCRYPTION_KEY=<fernet key>`.
   FastMCP wraps the Redis-backed `ClientStorage` with Fernet so secrets never
   land at-rest in plaintext.

The compose templates use option (2) and ship a dedicated `redis` service.

---

## 7. Configuration Contract — `.env.example`

Authoritative env surface. Follow the Outline `.env.example` style: section
banners, inline rationale for every non-obvious var, scaling presets where
relevant. Template:

```ini
# =============================================================================
# Shlink MCP Server — Environment Configuration
# =============================================================================
# Remote MCP server bridging a self-hosted Shlink instance to AI clients.
# OAuth 2.1 + PKCE (Entra / Google / generic OIDC) on the inbound side,
# static X-Api-Key on the outbound side.
#
# /!\ BEFORE FIRST START — replace EVERY value containing "CHANGE_ME" /!\
#
# One-shot, cross-platform (Windows / Linux / macOS):
#
#   python scripts/generate-env.py
#
# Deployment:
#   Local development:   docker compose -f docker-compose.development.yml up -d
#   Self-hosted Traefik: docker compose -f docker-compose.traefik.yml      up -d
#   Coolify:             configure env in dashboard → docker-compose.coolify.yml
# =============================================================================


# =============================================================================
# Stack Configuration
# =============================================================================

STACK_NAME=shlink-mcp
TIME_ZONE=Etc/UTC
ENVIRONMENT=production       # production | staging | development


# =============================================================================
# Image
# =============================================================================

SHLINK_MCP_IMAGE=ghcr.io/bauer-group/ip-shlink-mcpserver/shlink-mcp
SHLINK_MCP_VERSION=latest

# Redis (for OAuth client storage — required in production)
REDIS_IMAGE=redis
REDIS_VERSION=8-alpine


# =============================================================================
# Hostnames & URLs (Traefik / Coolify only)
# =============================================================================

# Public hostname of the MCP server — Let's Encrypt resolves this for HTTPS.
SHLINK_MCP_HOSTNAME=shlink-mcp.example.com

# External Traefik network name (must already exist on the Docker host)
PROXY_NETWORK=EDGEPROXY

# Full public base URL — used in OAuth redirect URIs and protected-resource
# metadata. MUST exactly match what your IdP has registered (case, scheme,
# trailing slash).
PUBLIC_BASE_URL=https://${SHLINK_MCP_HOSTNAME}


# =============================================================================
# Local Ports (development only)
# =============================================================================

SHLINK_MCP_PORT=8000


# =============================================================================
# Shlink Backend (REQUIRED, used for every outbound call)
# =============================================================================
# The MCP server addresses Shlink server-to-server. The API key is the trust
# anchor for that side — it has full read/write authority over the configured
# Shlink instance. Keep it in a secret store; never commit.

# Base URL of your Shlink REST API root (without /rest/v3 — the client adds it)
# Examples:
#   http://shlink:8080                       (same Docker network as Shlink)
#   https://urls.bauer-group.com             (public Shlink instance)
SHLINK_URL=http://shlink:8080

# Shlink API key — generate inside Shlink:
#   docker exec shlink shlink api-key:generate --name="mcp-server"
SHLINK_API_KEY=CHANGE_ME_SHLINK_API_KEY

# OpenAPI spec source — either a remote URL or a path inside the container.
# Default points at Shlink's published spec; switch to a pinned local file
# (mounted via volume) to lock to a specific Shlink version.
SHLINK_OPENAPI_URL=https://raw.githubusercontent.com/shlinkio/shlink-docs/main/openapi/swagger.json

# How often (seconds) to refresh the OpenAPI spec at runtime (0 = never)
SHLINK_OPENAPI_REFRESH_INTERVAL=3600


# =============================================================================
# MCP Server
# =============================================================================

# Transport — only streamable-http makes sense for a remote server.
# 'stdio' is supported for local debugging via Claude Desktop.
MCP_TRANSPORT=streamable-http

# Bind address inside the container — leave at 0.0.0.0 for Docker.
MCP_HOST=0.0.0.0
MCP_PORT=8000

# Per-request timeout to Shlink (seconds)
SHLINK_HTTP_TIMEOUT=30

# Log format: console (dev) or json (prod aggregator-friendly)
LOG_FORMAT=json
LOG_LEVEL=INFO


# =============================================================================
# Auth Mode — pick ONE
# =============================================================================
# Valid: entra-single | entra-multi | google | oidc | none
# 'none' requires ENVIRONMENT=development and prints a loud startup warning.

AUTH_MODE=entra-single


# =============================================================================
# Auth: Microsoft Entra ID — Single Tenant
# =============================================================================
# Setup:
#   1. portal.azure.com → Entra ID → App registrations → New registration
#   2. Single Tenant
#   3. Redirect URI: Web → ${PUBLIC_BASE_URL}/auth/azure/callback
#   4. API permissions → Microsoft Graph → Delegated:
#        email, offline_access, openid, profile  →  Grant admin consent
#   5. Certificates & secrets → New client secret → copy the *value*

ENTRA_CLIENT_ID=
ENTRA_CLIENT_SECRET=
ENTRA_TENANT_ID=

# Optional: extra scopes beyond the standard openid/profile/email/offline_access
ENTRA_EXTRA_SCOPES=


# =============================================================================
# Auth: Microsoft Entra ID — Multi-Tenant
# =============================================================================
# Use when AUTH_MODE=entra-multi.
# ENTRA_TENANT_ID becomes one of:
#   organizations   — accepts work/school accounts from any tenant
#   common          — accepts work/school + personal Microsoft accounts
#
# Without ENTRA_ALLOWED_TENANTS set, *anyone* with a Microsoft account in any
# tenant can call your MCP. Always set the allowlist in production.

ENTRA_ALLOWED_TENANTS=
# Example: ENTRA_ALLOWED_TENANTS=11111111-1111-1111-1111-111111111111,2222...


# =============================================================================
# Auth: Google Workspace
# =============================================================================
# Setup at https://console.cloud.google.com/apis/credentials
# Authorized redirect URI: ${PUBLIC_BASE_URL}/auth/google/callback

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Comma-separated list of Google Workspace domains allowed to authenticate.
# Tokens without a matching `hd` claim are rejected (blocks personal Gmail).
GOOGLE_ALLOWED_DOMAINS=
# Example: GOOGLE_ALLOWED_DOMAINS=bauer-group.com,bauer.com


# =============================================================================
# Auth: Generic OIDC (Authentik / Keycloak / Zitadel / Auth0 / Okta / …)
# =============================================================================
# Redirect URI: ${PUBLIC_BASE_URL}/auth/oidc/callback
#
# Either set OIDC_DISCOVERY_URL and the loader pulls endpoints automatically,
# or set each OIDC_*_URI explicitly. Discovery is preferred — it picks up
# JWKS rotations transparently.

OIDC_DISCOVERY_URL=
# Example: OIDC_DISCOVERY_URL=https://auth.bauer-group.com/application/o/shlink-mcp/.well-known/openid-configuration

OIDC_ISSUER=
OIDC_AUTH_URI=
OIDC_TOKEN_URI=
OIDC_JWKS_URI=
OIDC_USERINFO_URI=

OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=

OIDC_SCOPES=openid profile email
OIDC_USERNAME_CLAIM=preferred_username


# =============================================================================
# OAuth Persistence
# =============================================================================
# JWT signing key — used to sign tokens issued by FastMCP. Must survive
# restarts or every redeploy invalidates active sessions.
#   openssl rand -hex 32

AUTH_JWT_SIGNING_KEY=CHANGE_ME_AUTH_JWT_SIGNING_KEY

# Redis URL for OAuth client-registration storage.
# Leave empty to use in-memory storage (dev only — does not survive restarts).
AUTH_REDIS_URL=redis://redis:6379/0

# Fernet key for at-rest encryption of stored client secrets.
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
AUTH_STORAGE_ENCRYPTION_KEY=CHANGE_ME_AUTH_STORAGE_ENCRYPTION_KEY


# =============================================================================
# Rate Limiting (optional)
# =============================================================================
# Per-IP rate limit on MCP endpoints. Applied BEFORE OAuth validation to
# protect against credential-stuffing.

RATE_LIMITER_ENABLED=true
RATE_LIMITER_REQUESTS=600
RATE_LIMITER_WINDOW_SECONDS=60


# =============================================================================
# Observability
# =============================================================================

# Sentry error tracking (optional)
SENTRY_DSN=
SENTRY_ENVIRONMENT=${ENVIRONMENT}
SENTRY_TRACES_SAMPLE_RATE=0.05
```

`scripts/generate-env.py` must replace every `CHANGE_ME_*` placeholder with a
fresh random hex value. Port the existing script from `CS-Outline` verbatim;
the placeholder shape is intentional so `docker compose config` can validate
the file in CI without empty-var failures.

---

## 8. Source Code — Module-by-Module Contract

### `src/config.py` — Pydantic Settings model

```python
from enum import StrEnum
from typing import Annotated, Literal
from pydantic import HttpUrl, SecretStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


class AuthMode(StrEnum):
    ENTRA_SINGLE = "entra-single"
    ENTRA_MULTI = "entra-multi"
    GOOGLE = "google"
    OIDC = "oidc"
    NONE = "none"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Environment = Environment.PRODUCTION
    public_base_url: HttpUrl
    log_format: Literal["console", "json"] = "json"
    log_level: str = "INFO"

    # Shlink backend
    shlink_url: HttpUrl
    shlink_api_key: SecretStr
    shlink_openapi_url: HttpUrl = "https://raw.githubusercontent.com/shlinkio/shlink-docs/main/openapi/swagger.json"
    shlink_openapi_refresh_interval: int = 3600
    shlink_http_timeout: int = 30

    # MCP transport
    mcp_transport: Literal["streamable-http", "stdio"] = "streamable-http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # Auth
    auth_mode: AuthMode
    auth_jwt_signing_key: SecretStr
    auth_redis_url: str | None = None
    auth_storage_encryption_key: SecretStr | None = None

    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None
    entra_tenant_id: str | None = None
    entra_allowed_tenants: Annotated[list[str], Field(default_factory=list)]

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_allowed_domains: Annotated[list[str], Field(default_factory=list)]

    oidc_discovery_url: HttpUrl | None = None
    oidc_issuer: str | None = None
    oidc_auth_uri: HttpUrl | None = None
    oidc_token_uri: HttpUrl | None = None
    oidc_jwks_uri: HttpUrl | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid profile email"

    @model_validator(mode="after")
    def _validate_auth(self) -> "Settings":
        match self.auth_mode:
            case AuthMode.NONE:
                if self.environment is Environment.PRODUCTION:
                    raise ValueError("AUTH_MODE=none is forbidden in production")
            case AuthMode.ENTRA_SINGLE | AuthMode.ENTRA_MULTI:
                for name in ("entra_client_id", "entra_client_secret", "entra_tenant_id"):
                    if not getattr(self, name):
                        raise ValueError(f"{name.upper()} required for AUTH_MODE={self.auth_mode}")
            case AuthMode.GOOGLE:
                for name in ("google_client_id", "google_client_secret"):
                    if not getattr(self, name):
                        raise ValueError(f"{name.upper()} required for AUTH_MODE=google")
            case AuthMode.OIDC:
                has_discovery = self.oidc_discovery_url is not None
                has_explicit = all([self.oidc_auth_uri, self.oidc_token_uri, self.oidc_jwks_uri])
                if not (has_discovery or has_explicit):
                    raise ValueError("OIDC requires either OIDC_DISCOVERY_URL or all OIDC_*_URI vars")
                for name in ("oidc_client_id", "oidc_client_secret"):
                    if not getattr(self, name):
                        raise ValueError(f"{name.upper()} required for AUTH_MODE=oidc")
        return self
```

### `src/auth/provider_factory.py`

```python
from fastmcp.server.auth import AuthProvider

def build_auth_provider(settings: Settings) -> AuthProvider | None:
    match settings.auth_mode:
        case AuthMode.NONE:
            return None
        case AuthMode.ENTRA_SINGLE | AuthMode.ENTRA_MULTI:
            return build_entra_provider(settings)
        case AuthMode.GOOGLE:
            return build_google_provider(settings)
        case AuthMode.OIDC:
            return build_generic_oidc_provider(settings)
```

Each `build_*` lives in its own module so unit tests can mock the FastMCP
provider class without polluting the others.

### `src/shlink/client.py`

A thin `httpx.AsyncClient` wrapper:

* Single `X-Api-Key` header injected on every request.
* Base URL = `{SHLINK_URL}/rest/v3` (Shlink convention).
* Retries on `5xx` and connection errors (3 attempts, exponential backoff).
* Maps Shlink's structured error responses (`application/problem+json`) to
  Python exceptions: `ShlinkNotFound`, `ShlinkValidationError`, `ShlinkConflict`,
  `ShlinkAuthError`.
* Health check method `await client.health()` calls `/health` and returns the
  `status` string — used by the container HEALTHCHECK and the MCP resource.

### `src/shlink/openapi_loader.py`

* Fetch `SHLINK_OPENAPI_URL` once at startup, cache in memory.
* If `SHLINK_OPENAPI_REFRESH_INTERVAL > 0`, spawn a `asyncio.create_task` that
  refreshes every N seconds and **only** swaps the cached spec on a successful
  fetch (so a network blip never tears down the tool list).
* If the URL begins with `file://`, read locally (lets ops mount a pinned spec).

### `src/shlink/tool_mapper.py`

* Calls `FastMCP.from_openapi(...)` with the route maps from #5.
* Adds a custom `name_mapper` so generated tool names match the table in #5
  exactly (FastMCP's default is `operationId`-based; Shlink's `operationId`s
  are PascalCase and verbose).
* Attaches improved tool descriptions where Shlink's OpenAPI `summary` is
  uninformative — keep this list short and only override when the
  out-of-the-box description would confuse an LLM.

### `src/server.py`

```python
import asyncio
from contextlib import asynccontextmanager
from fastmcp import FastMCP

@asynccontextmanager
async def lifespan(app: FastMCP):
    settings = get_settings()
    setup_logging(settings)
    shlink_client = build_shlink_client(settings)
    spec = await load_shlink_spec(settings)
    tool_mapper.attach(app, spec, shlink_client)
    refresh_task = asyncio.create_task(
        spec_refresh_loop(settings, app, shlink_client)
    )
    try:
        yield
    finally:
        refresh_task.cancel()
        await shlink_client.aclose()


def build_app() -> FastMCP:
    settings = get_settings()
    auth = build_auth_provider(settings)
    return FastMCP(
        name="shlink-mcp",
        instructions="Self-hosted Shlink URL shortener — create, manage, and analyse short links.",
        auth=auth,
        lifespan=lifespan,
    )
```

### `src/main.py` — CLI entrypoint

```python
import typer

app = typer.Typer(no_args_is_help=False)

@app.callback(invoke_without_command=True)
def default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)

@app.command()
def serve() -> None:
    """Run the MCP server (default)."""
    settings = get_settings()
    server = build_app()
    server.run(
        transport=settings.mcp_transport,
        host=settings.mcp_host,
        port=settings.mcp_port,
    )

@app.command(name="tools")
def list_tools() -> None:
    """Print the auto-generated tool catalogue (for docs/tools.md)."""
    ...

@app.command()
def health() -> None:
    """Probe Shlink + IdP discovery and exit 0/1."""
    ...

if __name__ == "__main__":
    app()
```

---

## 9. Dockerfile

Mirror `src/outline-backup/Dockerfile` exactly — same multi-stage shape, same
test-gates-prod pattern, same `tini` PID 1, same non-root user. Differences:

* Drop `postgresql-client` (not needed).
* Add `curl` for the HTTP healthcheck.
* Expose 8000.
* Default `CMD` is the typer `serve` command.

```dockerfile
# =============================================================================
# Shlink MCP Server
# =============================================================================
# Remote MCP bridge for self-hosted Shlink. OAuth 2.1 (Entra / Google / OIDC)
# inbound, X-Api-Key outbound, FastMCP + streamable-http transport.
#
# Multi-stage build with integrated testing.
#
#   docker build --target prod -t shlink-mcp .         # production only
#   docker build --target test -t shlink-mcp-test .    # run tests
#   docker build -t shlink-mcp .                       # full pipeline (test gates prod)
# =============================================================================


# ── Stage 1: Builder — install Python dependencies ─────────────────────────
FROM python:3.14-alpine AS builder

RUN apk add --no-cache build-base libffi-dev

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Test — run pytest, build fails if tests fail ──────────────────
FROM python:3.14-alpine AS test

RUN apk add --no-cache build-base libffi-dev

WORKDIR /app

COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt

COPY src/ ./src/
COPY tests/ ./tests/
COPY pytest.ini ./

ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN env -i HOME="$HOME" PATH="$PATH" PYTHONPATH="$PYTHONPATH" \
        PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
        pytest tests/ -v --tb=short


# ── Stage 3: Production — minimal runtime image ─────────────────────────────
FROM python:3.14-alpine AS prod

# ── OCI / BAUER GROUP labels ───────────────────────────────────────────────
LABEL vendor="BAUER GROUP"
LABEL maintainer="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.title="Shlink MCP Server"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.authors="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/IP-Shlink-MCPServer"
LABEL org.opencontainers.image.url="https://github.com/bauer-group/IP-Shlink-MCPServer"
LABEL org.opencontainers.image.documentation="https://github.com/bauer-group/IP-Shlink-MCPServer#readme"
LABEL org.opencontainers.image.description="Shlink MCP Server — OIDC-gated remote MCP for self-hosted Shlink (BAUER GROUP)"
LABEL com.bauer-group.image.component="mcp-server"
LABEL com.bauer-group.image.stack="shlink"
LABEL org.opencontainers.image.version="0.1.0"

RUN apk add --no-cache \
        tini \
        tzdata \
        ca-certificates \
        curl \
        procps \
    && rm -rf /var/cache/apk/* \
    && addgroup -g 1001 -S mcp \
    && adduser -S -D -u 1001 -G mcp -h /home/mcp mcp

COPY --from=builder /install /usr/local

# Hard test-stage gate — if tests fail, prod cannot resolve this COPY.
COPY --from=test /app/pytest.ini /tmp/_test_passed
RUN rm /tmp/_test_passed

WORKDIR /app
COPY --chown=mcp:mcp src/ ./src/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER mcp
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

ENTRYPOINT ["/sbin/tini", "--", "python", "src/main.py"]
CMD ["serve"]
```

Add a tiny `/healthz` route in `server.py` that returns `200 OK` once startup
finished — distinct from the upstream Shlink health endpoint, which lives
behind the OAuth wall.

---

## 10. Docker Compose — Three Flavours

### Shared `x-logging` anchor (every file)

```yaml
x-logging: &logging
  logging:
    driver: json-file
    options:
      max-size: "50m"
      max-file: "3"
```

### 10.1 `docker-compose.development.yml`

* Direct host port `${SHLINK_MCP_PORT:-8000}:8000`.
* No Traefik labels, no proxy network.
* `redis` service exposed locally on `6379` for ease of inspection.
* `ENVIRONMENT=development`, `LOG_FORMAT=console`, `AUTH_MODE=none` allowed.
* Document an optional `shlink` service stanza that pulls the image from
  `bauer-group/CS-URLShortener` so a developer can spin up an end-to-end stack
  with one command. Keep it commented-out by default so dev runs lean.

### 10.2 `docker-compose.traefik.yml`

Header comment (mirror Outline style):

```yaml
# =============================================================================
# Shlink MCP Server — Production Stack (Traefik Reverse Proxy)
# =============================================================================
# Usage:  docker compose -f docker-compose.traefik.yml up -d
#
# Prerequisites:
#   - Traefik v2/v3 reverse proxy running on the ${PROXY_NETWORK} network
#   - DNS record: ${SHLINK_MCP_HOSTNAME} → this host
#   - A reachable Shlink instance — same Docker network or public URL
#   - An OIDC provider App Registration / OAuth client configured
#
# Architecture:
#       AI Clients
#            │  HTTPS (OAuth 2.1, /mcp)
#            ▼
#       ┌─────────┐
#       │ Traefik │ — proxy network (external)
#       └────┬────┘
#            │
#       ┌────▼──────────────┐
#       │ shlink-mcp ─ redis│ — local network
#       └───────────────────┘
# =============================================================================
```

Services:

* `shlink-mcp` — joins both `proxy` (external) and `local`, Traefik labels for
  HTTP→HTTPS redirect plus HTTPS router on Host(`${SHLINK_MCP_HOSTNAME}`).
* `redis` — internal only, for the OAuth client store. Use the same hardened
  `redis:8-alpine` settings as Outline (`appendonly yes`, `maxmemory-policy
  noeviction`, optional `REDIS_PASSWORD`).

Traefik labels (single-router):

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.docker.network=${PROXY_NETWORK:-EDGEPROXY}"

  # HTTP → HTTPS redirect
  - "traefik.http.middlewares.${STACK_NAME:-shlink-mcp}-redirect.redirectscheme.scheme=https"
  - "traefik.http.middlewares.${STACK_NAME:-shlink-mcp}-redirect.redirectscheme.permanent=true"

  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-http.rule=Host(`${SHLINK_MCP_HOSTNAME}`)"
  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-http.entrypoints=web"
  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-http.middlewares=${STACK_NAME:-shlink-mcp}-redirect"

  # HTTPS
  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-app.rule=Host(`${SHLINK_MCP_HOSTNAME}`)"
  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-app.entrypoints=web-secure"
  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-app.tls=true"
  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-app.tls.certresolver=letsencrypt"
  - "traefik.http.routers.${STACK_NAME:-shlink-mcp}-app.service=${STACK_NAME:-shlink-mcp}-app"
  - "traefik.http.services.${STACK_NAME:-shlink-mcp}-app.loadBalancer.server.port=8000"
```

Critically: **streamable HTTP is just POST + Server-Sent chunks** — Traefik
needs no special middleware. Don't add `buffering` middlewares or large-body
limits unless someone reports an issue; the default is correct.

### 10.3 `docker-compose.coolify.yml`

* No host ports.
* No Traefik labels — Coolify auto-generates routers from the
  `coolify.managed=true` label and reads exposed ports.
* Add Coolify-specific labels:

```yaml
labels:
  - "coolify.managed=true"
  - "coolify.type=application"
  - "coolify.name=shlink-mcp"
```

* Resource limits suggested in the Coolify dashboard, not hard-coded.

### Volumes & Networks (all three files)

```yaml
volumes:
  redis-data:
    driver: local
    name: ${STACK_NAME:-shlink-mcp}-redis

networks:
  local:
    driver: bridge
    name: ${STACK_NAME:-shlink-mcp}
    enable_ipv6: true

  proxy:                                # only in traefik.yml
    name: ${PROXY_NETWORK:-EDGEPROXY}
    external: true
```

---

## 11. GitHub Workflows

Copy the five workflow files from `bauer-group/CS-Outline` 1:1 and make the
following targeted edits.

### 11.1 `docker-release.yml`

Update three fields, leave everything else:

```yaml
validate-compose:
  with:
    compose-files: '["docker-compose.coolify.yml", "docker-compose.traefik.yml", "docker-compose.development.yml"]'
    env-file: '.env.example'
    validate-services: '["shlink-mcp", "redis"]'

docker-build-shlink-mcp-release:
  name: 🐳 Build & Push shlink-mcp
  with:
    ghcr-image-name: 'bauer-group/IP-Shlink-MCPServer/shlink-mcp'
    docker-image-name: 'bauergroup/ip-shlink-mcp'
    dockerfile-path: './src/shlink-mcp/Dockerfile'
    docker-context: './src/shlink-mcp'
    platforms: 'linux/amd64,linux/arm64'    # bump to multi-arch — pure Python, free
    # ...rest mirrors CS-Outline

docker-build-shlink-mcp-pr:
  # mirror CS-Outline outline-backup-pr job
```

Add a step **after** `docker-build-shlink-mcp-release` that generates
`docs/tools.md` and opens a follow-up PR if it changed. The job runs the image
once with `python src/main.py tools --output docs/tools.md` and uses
`peter-evans/create-pull-request@v6`.

### 11.2 `docker-maintenance.yml`

Path filter changes from `src/outline-backup/Dockerfile` to:

```yaml
on:
  pull_request:
    paths:
      - 'src/shlink-mcp/Dockerfile'
```

### 11.3 `check-base-images.yml`

Reuses the same reusable module. Update only:

* `.github/config/docker-base-image-monitor/base-images.json` — point at the
  new Dockerfile path.

### 11.4 `ai-issue-summary.yml`, `teams-notifications.yml`

Copy unchanged.

### 11.5 `dependabot.yml`

Add the `docker` and `pip` ecosystems:

```yaml
version: 2
updates:
  - package-ecosystem: docker
    directory: /src/shlink-mcp
    schedule:
      interval: weekly
    commit-message:
      prefix: chore(deps)

  - package-ecosystem: pip
    directory: /src/shlink-mcp
    schedule:
      interval: weekly
    commit-message:
      prefix: chore(deps)

  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: monthly
    commit-message:
      prefix: chore(ci)
```

---

## 12. Documentation Pages

| File | Audience | Content outline |
| --- | --- | --- |
| `README.md` | Public, GitHub front page | Mission, highlights table (mirror Outline), architecture diagram, quick start (three compose flavours), repo layout, supported versions, troubleshooting top-N, license |
| `docs/installation.md` | Operator | Step-by-step deploy for each of the three flavours, DNS preflight checklist, first-call verification with `curl` against `/.well-known/oauth-protected-resource` |
| `docs/authentication.md` | Operator | Four full walkthroughs (Entra single, Entra multi, Google, generic OIDC). Mirror Outline's authentication.md style — screenshots optional, redirect-URI tables mandatory |
| `docs/client-setup.md` | End user | How to add the connector inside Claude Web (Settings → Connectors → Add custom), Claude Desktop (`mcp.json` snippet), Microsoft 365 Copilot Studio, ChatGPT Connectors. Each section shows the exact URL and OAuth flow screenshots |
| `docs/tools.md` | Public | Auto-generated catalogue of tools (CI artefact — see #11.1). Group by category, show input schema + example call |
| `docs/troubleshooting.md` | Operator | Common errors table — bad redirect URI, expired JWT signing key, Shlink 401, Coolify router conflict, Traefik 404 on `/.well-known/...` |

---

## 13. Definition of Done

The first release (`v0.1.0`, tagged automatically by semantic-release) must
satisfy every box below. Mirror the BAUER GROUP standard, no exceptions.

* [ ] `docker compose -f docker-compose.development.yml up -d` boots in <15 s
* [ ] `docker compose -f docker-compose.traefik.yml config` passes without
      `:?` failures when `.env.example` is the env file
* [ ] `docker compose -f docker-compose.coolify.yml config` passes
* [ ] `pytest` is green in the test stage of the multi-stage build
* [ ] Test coverage ≥ 80% on `config.py`, `auth/`, `shlink/client.py`
* [ ] `curl -fsS https://<host>/.well-known/oauth-protected-resource` returns
      RFC 9728 JSON pointing at the correct authorization server
* [ ] An unauthenticated POST to `/mcp` returns `401` with a `WWW-Authenticate`
      header that includes `resource_metadata=`
* [ ] Adding the connector in **Claude Web** completes the OAuth dance and
      the test query `"list my 5 newest short links"` returns Shlink data
* [ ] Same flow works with **Claude Desktop** via the connector URL
* [ ] Same flow works with **Microsoft 365 Copilot Studio** custom connector
* [ ] Restarting the container does not log existing users out (Redis-backed
      client store survives)
* [ ] `docs/tools.md` is generated by CI and matches the live Shlink spec
* [ ] GHCR image at `ghcr.io/bauer-group/ip-shlink-mcpserver/shlink-mcp:latest`
      is multi-arch (`linux/amd64`, `linux/arm64`)
* [ ] `dependabot.yml` is wired for `docker`, `pip`, and `github-actions`
* [ ] Semantic-release tags a `v0.1.0` automatically on first merge to `main`

---

## 14. Build Sequence

A pragmatic order for the developer — each phase is small enough to commit and
deploy independently.

1. **Skeleton** — repo init, `LICENSE`, `.gitignore`, `.dockerignore`, copy the
   five workflow files from `CS-Outline`, edit the names per #11. `.env.example`
   from #7 (placeholders only, no logic yet).
2. **Generate-env helper** — port `scripts/generate-env.py` from `CS-Outline`,
   verify it rewrites `.env` correctly on Windows + Linux + macOS.
3. **Python skeleton** — `src/shlink-mcp/` with `pyproject.toml`, `requirements*.txt`,
   `pytest.ini`, empty `src/` and `tests/` packages, a no-op `main.py` that
   prints "ok" and exits.
4. **Dockerfile + dev compose** — get the multi-stage build green, the dev
   compose to bring the container up, `/healthz` returning 200.
5. **Config + Shlink client** — Pydantic settings model with the
   `_validate_auth` model-validator, `ShlinkClient` with retries and error
   mapping, tests for both. No FastMCP yet.
6. **OpenAPI loader + tool mapper** — fetch and cache the Shlink spec, wire
   `FastMCP.from_openapi()` with the route maps from #5. Run with
   `AUTH_MODE=none` (dev only) and verify tools show up in `npx mcp-inspector`.
7. **Auth providers** — implement the four providers in `src/auth/`, plug
   `build_auth_provider` into `build_app`. Test each mode end-to-end with a
   real IdP App Registration.
8. **Redis-backed persistence** — wire `AUTH_REDIS_URL` + encryption key,
   verify token survival across container restarts.
9. **Traefik + Coolify compose** — finalise both with full label sets, validate
   in CI.
10. **Docs pass** — write the four operator-facing docs, generate
    `docs/tools.md` via the CI step, polish `README.md`.
11. **First tag** — squash any noise into a single `feat:` commit, push to
    `main`, semantic-release fires `v0.1.0`, GHCR image lands.

---

## 15. Open Questions for the Operator (not the developer)

These are for the team to answer **before** going live, not for the developer
to guess at during the build:

* Which BAUER GROUP Entra tenant owns the App Registration? Single-tenant is
  recommended for the production deployment.
* What is the production hostname? Suggested: `shlink-mcp.bauer-group.com`.
* Who rotates `SHLINK_API_KEY`, on what cadence, where does it live (1Password
  vault item)?
* Where does Sentry land (existing BAUER GROUP project, or a dedicated one)?

---

## 16. References

* Reference repository for structure & workflows — [bauer-group/CS-Outline](https://github.com/bauer-group/CS-Outline)
* FastMCP — [github.com/jlowin/fastmcp](https://github.com/jlowin/fastmcp) · [gofastmcp.com](https://gofastmcp.com/)
* FastMCP Google integration — [gofastmcp.com/integrations/google](https://gofastmcp.com/integrations/google)
* MCP spec (Streamable HTTP) — [modelcontextprotocol.io](https://modelcontextprotocol.io/specification/2025-06-18/basic)
* Claude Web Connectors — [support.claude.com/en/articles/11175166](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
* Shlink OpenAPI spec — [api-spec.shlink.io](https://api-spec.shlink.io/) · [REST docs](https://shlink.io/documentation/api-docs/)
* OAuth 2.1 — [draft-ietf-oauth-v2-1](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1)
* RFC 9728 — Protected Resource Metadata — [datatracker.ietf.org/doc/rfc9728](https://datatracker.ietf.org/doc/rfc9728)

---

*Maintained by BAUER GROUP · Today, Tomorrow, Together*
