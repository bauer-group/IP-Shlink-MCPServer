# Vision: From Shlink-MCP to a Generic OpenAPI → MCP Bridge

> **Status:** architectural sketch, not committed scope. Captures the design
> space, trade-offs, and an incremental path so we can decide *if* and *how*
> to generalise the current single-tenant `bg-shlink-mcp` into a multi-API
> framework. Read this before adding ad-hoc support for a second upstream.

---

## 1 · Where we stand today

`bg-shlink-mcp` is a single-purpose MCP bridge: one OpenAPI spec in,
OAuth-gated MCP endpoint out. Everything is hard-coded for Shlink:

| Concern | Today's implementation | Where it lives |
|---|---|---|
| Spec source | `SHLINK_OPENAPI_URL` env var (file:// or http(s)://) | `src/config.py`, `src/shlink/openapi_loader.py` |
| Spec bundling | Build-time `bundle-openapi.py` + runtime `_resolve_external_refs` | `scripts/bundle-openapi.py`, `openapi_loader.py` |
| Tool naming | Hand-written `(method, path) → friendly_name` dict | `src/shlink/tool_mapper.py:NAME_OVERRIDES` |
| Tool descriptions | Hand-written name → text dict (3 entries) | `tool_mapper.py:DESCRIPTION_OVERRIDES` |
| Tool annotations | HTTP method → safety hints (GET=readonly, POST=destructive) | `tool_mapper.py:METHOD_ANNOTATIONS` |
| Route filtering | Regex list for excluded paths and resource-only paths | `tool_mapper.py:SHLINK_ROUTE_MAPS` |
| Inbound auth | Entra/Google/OIDC/none via `AuthMode` enum | `src/auth/provider_factory.py` |
| Outbound auth | One static `X-Api-Key` header per server instance | `src/shlink/client.py` |
| Identity mapping | None — server impersonates a single Shlink account | n/a |

The shape is already a generic-bridge-in-disguise: FastMCP's `from_openapi`
does the heavy lifting, and our customisations are a thin layer of
domain-specific overrides. The question is whether to make that layer
**declarative + multi-tenant** without rewriting from scratch.

---

## 2 · What "generic" actually means — three independent axes

Don't conflate these. Each is a separate decision with different
operational consequences.

### 2.1 · Axis A — Discovery: where does the spec come from?

| Mode | Trigger | Use case |
|---|---|---|
| **Static config** | Operator edits a YAML profile → restart | Stable internal APIs, pinned spec versions |
| **Runtime URL** | Profile says `openapi_url: https://...`, fetched on boot | Vendor APIs that publish a spec endpoint |
| **Self-discovery** | Only `base_url` given; bridge probes `/openapi.json`, `/swagger.json`, `/v3/api-docs`, `/.well-known/openapi` | "Just point me at the API" UX |
| **Registry / catalogue** | External index (e.g. APIs.guru, internal Backstage) | Fleet of APIs, central governance |
| **Client-supplied** | MCP client passes spec URL at session start | Power-user mode; security risk if unrestricted |

The current `bg-shlink-mcp` is mode 1 (with an embedded build-time bundle).
A generic bridge needs at minimum modes 1 + 2; mode 3 is the "killer feature"
for ad-hoc adoption.

### 2.2 · Axis B — Multiplexing: one server for N APIs, or N servers for N APIs?

| Pattern | Topology | Pros | Cons |
|---|---|---|---|
| **Sidecar / per-API** (current) | 1 MCP server per upstream, scale horizontally | Clean isolation, separate failure domain, separate OAuth scopes | N containers, N OAuth client registrations, operational overhead grows linearly |
| **Multi-mount gateway** | 1 MCP server, each API mounted at a tool-name prefix (`shlink.create_short_url`, `gitlab.list_projects`) | Single endpoint, single OAuth surface, single deployment | Single failure domain, OAuth scopes get coarse-grained, tool namespace pollution as APIs grow |
| **Federated** | Gateway routes to per-API workers behind the scenes | Single client endpoint + isolation per worker | Most operational complexity, two layers to debug |

**Recommendation if generalising**: start with **multi-mount gateway** for
small numbers (≤10 APIs, ≤500 total tools) — it's the simplest model that
unblocks 95 % of use cases. Move to federated only when a specific upstream
needs to scale independently or has incompatible failure characteristics.

FastMCP supports multi-mounting natively (`mcp.mount(prefix, sub_mcp)`),
so this is mostly a configuration story, not a code rewrite.

### 2.3 · Axis C — Auth-mapping: MCP user → upstream credential

This is the hairiest axis. The current model (one static API key, server
impersonates one Shlink account) doesn't scale because *who* called the MCP
tool becomes invisible to the upstream — audit, rate-limiting per-user, and
authorisation all collapse.

| Strategy | How | When to use |
|---|---|---|
| **Static service account** (current) | One credential per upstream, used for every MCP user | Single-tenant tools, internal admin APIs, when the upstream has no per-user identity |
| **Per-user mapping** | DB table `(mcp_user_id, upstream_id) → encrypted_credential` | Bring-your-own-key flows; each user provides their upstream API key once |
| **OAuth token exchange (RFC 8693)** | Trade the user's MCP access token for an upstream-scoped token at runtime | Upstream supports OIDC and trusts the same IdP — the cleanest model |
| **On-behalf-of (OBO)** | Microsoft-specific OAuth flow, similar to RFC 8693 | Microsoft Graph and other Azure-hosted APIs |
| **Pass-through bearer** | Forward the user's MCP access token unchanged to the upstream | Same auth boundary both sides — rare; mostly for internal microservices |
| **Per-tool credential** | Different credential for read vs. write tools | Privilege separation within one upstream |

The right choice depends on the **trust shape** between MCP-IdP and upstream
API, not on us. A generic bridge needs to support at least static + per-user
+ token-exchange to be operationally useful.

---

## 3 · The minimum-viable generalisation

Three concrete components, in order of dependency:

### 3.1 · Profile: the per-upstream contract

Move every Shlink-specific dict and regex from `tool_mapper.py` into a YAML
declaration. One profile per upstream.

```yaml
# profiles/shlink.yaml
id: shlink                                # tool-namespace prefix
display_name: Shlink URL Shortener
description: |
  Self-hosted Shlink. Use these tools to create, list, edit, and analyse
  short URLs, manage tags and domains, inspect visits.

spec:
  source: file:///app/openapi/shlink.json
  refresh_interval_seconds: 0
  bundle_at_build: true                   # baked into image (mode 1)

upstream:
  base_url: https://go.example.com
  auth:
    type: static_header
    header: X-Api-Key
    value_from_env: SHLINK_API_KEY        # never put secrets in the profile
  http_timeout: 30

routes:
  - pattern: '^/rest/v(?:\d+|\{[^}]+\})/health$'
    type: resource
  - pattern: '^/rest/v(?:\d+|\{[^}]+\})/mercure-info$'
    type: exclude
  - pattern: '^/rest/v(?:\d+|\{[^}]+\})/rules'
    type: exclude

names:                                     # (METHOD, normalised_path) → snake_case
  - { method: POST,   path: /short-urls,                       name: create_short_url }
  - { method: GET,    path: /short-urls,                       name: list_short_urls }
  - { method: GET,    path: /short-urls/{shortCode},           name: get_short_url }
  - { method: PATCH,  path: /short-urls/{shortCode},           name: edit_short_url }
  - { method: DELETE, path: /short-urls/{shortCode},           name: delete_short_url }
  # ... (rest of the current NAME_OVERRIDES)

descriptions:
  create_short_url: |
    Create a new short URL. Provide longUrl (required) and optionally
    customSlug, tags, domain, validSince, validUntil, maxVisits, title.
  # ... 

annotations:                              # method → MCP safety hints (defaults if omitted)
  GET:    { readOnly: true,  destructive: false, openWorld: true }
  POST:   { readOnly: false, destructive: true,  openWorld: true }
  PUT:    { readOnly: false, destructive: true,  idempotent: true,  openWorld: true }
  PATCH:  { readOnly: false, destructive: true,  idempotent: true,  openWorld: true }
  DELETE: { readOnly: false, destructive: true,  idempotent: true,  openWorld: true }
```

Reasonable defaults make the profile **mostly empty for vanilla APIs**:
omit `names` → operationIds become snake_case automatically; omit
`descriptions` → use `operation.summary` from the spec; omit `annotations` →
HTTP-method defaults (the table above) apply.

A YAML schema (`profile.schema.json`) keeps profiles validate-able in CI
and editor-friendly.

### 3.2 · Registry: how the server discovers profiles

One env var pointing at a directory:

```bash
MCP_BRIDGE_PROFILES_DIR=/etc/mcp-bridge/profiles.d
```

On boot the server reads `*.yaml` from that directory, instantiates one
sub-MCP per profile, and mounts them at `<profile.id>.*` prefixes on a
gateway parent server. Hot-reload on file change (watchdog → fastmcp
`mount/unmount`) is a Phase-2 nice-to-have, not required.

Three deployment shapes drop out for free:

- **Standalone**: one profile in the directory → single-tenant behaviour
  (today's `bg-shlink-mcp` mode)
- **Compose stack**: ship a `profiles.d/` volume with multiple `.yaml` files
  → multi-tenant gateway
- **Kubernetes**: a ConfigMap mounted as `profiles.d/`, optionally managed
  by a `MCPBridge` CRD + operator

### 3.3 · Auth resolver: a pluggable strategy per profile

Each profile declares its outbound auth shape (see Axis C above). The
runtime translates `upstream.auth` into a callable that produces the
correct headers for each outbound request, given the **MCP request context**
(which includes the validated inbound user identity from FastMCP's auth
provider).

```python
class AuthResolver(Protocol):
    async def headers_for(self, ctx: McpRequestContext) -> dict[str, str]: ...
```

Built-in resolvers:

- `StaticHeaderResolver` — current Shlink behaviour
- `BearerEnvResolver` — `Authorization: Bearer ${env:UPSTREAM_TOKEN}`
- `PerUserCredentialResolver` — looks up `(user_id, profile_id)` in
  an encrypted store, returns the user's stored credential
- `TokenExchangeResolver` — RFC 8693 exchange against the inbound token's
  issuer, with the upstream's audience claim
- `PassThroughBearerResolver` — forwards the inbound bearer unchanged
  (use only when MCP and upstream share an auth boundary)

A profile picks one. The resolver is invoked per-request, not per-server-
boot, so per-user strategies just work.

---

## 4 · What this unlocks (and what it doesn't)

### 4.1 · Genuinely solved by this design

- **Adding a vanilla REST API** that ships a sane OpenAPI spec becomes a
  ~30-line YAML file, no Python change.
- **Multi-tenancy at the deployment level** — one gateway, N upstreams,
  one OAuth registration per IdP.
- **Per-user credentials** for upstreams that have a per-user identity,
  via `PerUserCredentialResolver` + an admin UI for the user to register
  their credential.
- **Operational consistency** — same `/healthz`, same logging schema, same
  metrics surface for every upstream.

### 4.2 · Still hard / out of scope

- **Spec quality**: OpenAPI specs in the wild are routinely broken (invalid
  `$ref` chains, missing `responses`, vendor extensions FastMCP doesn't
  understand). A generic bridge can only fail loudly, not magically fix.
- **Non-OAuth upstreams**: AWS SigV4, mTLS, signed-URL APIs need their own
  resolver implementations. Designable, not free.
- **Pagination semantics**: many APIs use `nextPageToken` / `Link: rel=next`
  / cursor headers. LLMs don't intuitively chain pages. Per-tool adapters
  (or a `ChainedListTool` wrapper) are a separate concern.
- **Tool-surface explosion**: Slack's OpenAPI has 200+ operations. Dumping
  all of them into an MCP session bloats the model's context. We'd need
  **per-tenant tool allow-lists** + **on-demand tool loading** (FastMCP's
  `tags` filtering helps; not enough on its own).
- **Authorisation policy**: who can see / call which tool? RBAC + ABAC is
  a separate subsystem on top of identity.
- **Side-effect safety**: the MCP `destructiveHint` annotation is exactly
  that — a hint. For real safety we'd need server-side guards: dry-run
  mode, idempotency-key injection, audit logging per destructive call.

### 4.3 · Open questions worth resolving before committing

1. **Single domain or one per profile?** `https://mcp.example.com/shlink/mcp`
   vs `https://shlink-mcp.example.com/mcp`. Affects OAuth redirect URI
   registration cost and CORS.
2. **How does a user pick which tools to expose?** All by default, or
   opt-in per-tool? Affects context budget for the LLM.
3. **Spec-change semantics**: if the upstream spec adds a new endpoint, do
   we auto-expose it or require an explicit profile update? Auto is
   convenient, explicit is safer (new endpoints might leak data).
4. **Versioning the profile schema**: profiles are config; they need their
   own SemVer story so the bridge can warn on incompatible schemas.

---

## 5 · An incremental path that doesn't break the current product

Each phase is independently shippable and reversible. Don't do them all at
once.

| Phase | Scope | Risk | Done when |
|---|---|---|---|
| **0 — Codify current behaviour as a profile** | Move Shlink overrides out of `tool_mapper.py` into `profiles/shlink.yaml`, loaded at boot. Server code becomes profile-agnostic but still single-tenant. | Low. Behaviour-preserving refactor. | All current tests pass; `bg-shlink-mcp tools` output is byte-identical. |
| **1 — Multi-profile support, single deployment** | Boot N profiles from a directory, mount each at `<profile.id>.*`. Add `profiles.d/` to compose files. | Medium. New mount logic, but FastMCP supports it. OAuth surface stays single. | Two profiles in the same container expose two tool namespaces; old single-profile mode still works (one file in the dir). |
| **2 — Pluggable auth resolvers** | Introduce `AuthResolver` protocol + `StaticHeaderResolver` (Shlink path) + `BearerEnvResolver`. | Low–Medium. Refactor of `shlink/client.py` into a generic `upstream_client.py`. | Profile selects auth strategy declaratively; Shlink profile uses `static_header` and still works. |
| **3 — Discovery mode** | Profile can omit `spec.source` and supply only `upstream.base_url`; bridge probes well-known OpenAPI endpoints. | Low. Pure additive. | A profile with `base_url: https://api.example.com` + nothing else boots and serves the discovered spec's tools. |
| **4 — Per-user credentials** | `PerUserCredentialResolver` + encrypted store (reuse `AUTH_STORAGE_ENCRYPTION_KEY` + Redis from existing code) + minimal admin endpoint for users to register credentials. | High. New persistence, new endpoints, new attack surface. | A user can register their own upstream API key, and subsequent MCP tool calls use it. |
| **5 — Token exchange (RFC 8693)** | `TokenExchangeResolver` for upstreams that share the MCP IdP. | High. Touches the most complex part of the OAuth spec. | A user calls a tool, the bridge exchanges their inbound token for a downstream-audience token, and the upstream sees the user's identity. |
| **6 — Hot reload + admin API** | File-watch on `profiles.d/`, optional `POST /admin/profiles` for runtime registration. | Medium. Concurrency around mount/unmount. | Adding a profile YAML file makes its tools available without restart. |
| **7 — Authorisation policy** | RBAC layer mapping MCP-user roles to allowed profiles and tools. | High. Needs a real policy model (e.g. OpenFGA, Casbin, or a simple roles-claim mapping). | Two users with different roles see different `tools/list` responses against the same gateway. |

Phase 0 alone is genuinely useful — it makes adding a *second* Shlink-shaped
API a config-file affair. Phases 1–3 turn the product into "give me a
working OpenAPI and I'll give you an MCP." Phases 4+ are where it becomes
a platform.

---

## 6 · Naming and positioning

If we go past Phase 1, the product is no longer `bg-shlink-mcp`. Likely
shape:

- **Repository / image**: `bg-mcp-bridge`
- **Binary / CLI**: `bg-mcp-bridge`
- **Current Shlink integration**: shipped as a built-in profile
  (`profiles/builtin/shlink.yaml`) plus a "with Shlink" preset compose file
- **Tagline**: *"Any OpenAPI, any IdP, one MCP endpoint."*

The Shlink-specific image stays buildable from the same repo (it's the
single-profile preset). No one is forced to migrate.

---

## 7 · Recommendation

**Do Phase 0 now, only if a second upstream is on the horizon.** Phase 0
is a clean refactor with no operational risk, and it removes the
"hard-coded Shlink" smell that would otherwise force a rewrite later.
Don't speculate past Phase 0 until a real second use case exists — every
phase past 0 trades flexibility for new failure modes, and the right shape
of phases 1–4 is impossible to design without two concrete profiles to
compare.

If no second upstream is on the roadmap, **don't generalise**. The current
implementation is right-sized for its job; over-engineering at this size is
its own kind of debt.
