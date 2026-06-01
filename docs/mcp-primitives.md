<!-- markdownlint-disable MD024 -->
# MCP Primitives & Server Features — Implementation Guide

Companion reference for the eleven tabs you see in the MCP Inspector toolbar
(**Resources · Prompts · Tools · Tasks · Apps · Ping · Sampling · Elicitations ·
Roots · Auth · Metadata**). For each one this guide answers:

1. **What it is** — the semantic role in the protocol
2. **Who triggers it** — AI / user / server / transport
3. **When to use it** — and when to use something else
4. **FastMCP 3.x API** — concrete decorator / Context call
5. **Status in `bg-shlink-mcp`** — implemented, partial, or unused
6. **Pitfalls** — the mistakes that bite once you ship

> **Source of truth:** the [MCP specification](https://spec.modelcontextprotocol.io)
> defines the wire protocol. FastMCP 3.x is one server implementation among
> several; the Python decorators below are FastMCP-specific, but the protocol
> concepts apply to every MCP server regardless of language.

---

## TL;DR — the eleven tabs at a glance

| Tab | Category | Who triggers? | Status here | One-line summary |
| --- | --- | --- | --- | --- |
| **Tools** | Component primitive | AI in the turn | **implemented** (25 spec-driven + 1 export task) | The AI calls them to *do* something |
| **Resources** | Component primitive | User / client pins them | **implemented** (4 — config-driven) | The AI *reads* them like files |
| **Resource Templates** | Component primitive | User picks a URI | **implemented** (6 — config-driven) | Parameterised resources (`shlink://short-url/{shortCode}`) |
| **Prompts** | Component primitive | User picks a workflow | **implemented** (1 — config-driven) | Pre-baked multi-tool workflows (slash-command-like) |
| **Apps** | UI extension on a component | Client renders | **not used** (0) | Tool/resource emits an HTML/UI snippet, client renders sandboxed |
| **Tasks** | Execution mode | AI opts in | **implemented** (1 — `export_short_urls`) | Long-running tool calls — return task-ID, poll later |
| **Ping** | Protocol plumbing | Either side | **automatic** | Keepalive — no app code needed |
| **Sampling** | Server→client capability | Server, inside a tool | **not used** | Server asks the client's LLM for a completion |
| **Elicitations** | Server→client capability | Server, inside a tool | **not used** | Server asks the user for missing input |
| **Roots** | Client→server capability | Client advertises | **not applicable** | Remote MCP — no shared filesystem with the client |
| **Auth** | Cross-cutting | Transport + per-component | **implemented** (OAuth 2.1) | Inbound identity gate + per-tool scope checks |
| **Metadata** | Cross-cutting | Any component | **implemented** (annotations + tags) | `_meta` / annotations carry hints the protocol doesn't standardise |

**Three layers in one toolbar.** The Inspector mixes things at different
abstraction levels:

- **Component primitives** (Tools, Resources, Resource Templates, Prompts,
  Apps) — registered things the server exposes
- **Execution modes & flows** (Tasks, Sampling, Elicitations, Roots) — *how*
  a call runs, not *what* runs
- **Plumbing** (Ping, Auth, Metadata) — protocol/transport mechanics

When you design, decide in that order: *which primitive*, then *which mode*,
then *which plumbing knobs*.

---

## 1. Tools — "the AI does something"

### Concept

A **callable function** exposed to the AI. The AI sees a `name`, a description,
a JSON-Schema `inputSchema`, optional annotations (read-only? destructive?
idempotent?), and decides — *inside the turn* — whether to invoke it. The
client passes arguments matching the schema; the server returns content
and/or `structured_content`.

### When to use it

- The operation has side effects (`POST`, `PATCH`, `DELETE`, `PUT`)
- The result depends on **arguments the AI must reason about** (search term,
  date range, filter set)
- The AI should decide *whether* and *when* to run it

If none of those apply, consider a Resource instead — it carries less
cognitive overhead for the AI and is auto-safe (read-only by definition).

### FastMCP API

```python
@mcp.tool
async def create_short_url(longUrl: str, customSlug: str | None = None) -> dict:
    """Create a new short URL on the configured Shlink instance."""
    ...
```

Or with explicit metadata:

```python
@mcp.tool(
    name="list_short_urls",
    description="List short URLs, optionally filtered…",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    tags={"shlink", "read"},
    auth=AuthCheck(scopes=["shlink.read"]),     # per-tool scope gate
    task=False,                                  # sync execution
    timeout=30.0,                                # hard cap
)
async def list_short_urls(...) -> dict:
    ...
```

### Status in `bg-shlink-mcp`

**Implemented — 25 tools, auto-generated from Shlink's OpenAPI.**

We do not hand-write tool functions. bg-mcpcore's OpenAPI tool source calls
`FastMCP.from_openapi()` to walk the spec and register every operation as a
tool, configured declaratively by the `tools` block in
[profiles/shlink.json](../app/bg-shlink-mcp/src/profiles/shlink.json). The same
source applies four post-processing passes, all driven from that profile:

| Pass | What it does | Profile key |
| --- | --- | --- |
| spec normalisation | Strips `/rest/v{version}` from paths and drops the `version` path-param so the LLM never sees it | `normalize.strip_path_prefix` |
| route maps | Filters routes — `/mercure-info` excluded, `/health` becomes a Resource | `route_maps` |
| name overrides | Maps `operationId`s to snake_case (`createShortUrl` → `create_short_url`) | `name_overrides` |
| annotations | Per-HTTP-method safety hints applied via the component hook | `annotations: by_http_method` |

### Pitfalls

- **Annotations are hints, not authorisation.** Clients may ignore
  `destructiveHint`. Treat them as defence-in-depth; keep the Shlink API key
  on a need-to-know basis regardless.
- **A "useful description" doubles the AI's hit rate.** Shlink's spec
  summaries are sometimes uninformative ("List short URLs"). The profile's
  `descriptions` map ([profiles/shlink.json](../app/bg-shlink-mcp/src/profiles/shlink.json))
  overrides them for exactly this reason on the three most-used tools.
- **GET-as-Tool is a mild smell.** ~10 of our 25 tools are pure reads. They
  work as tools, but a Resource Template would be cheaper in tokens and
  semantically more honest. See §3.

---

## 2. Resources — "the AI reads something"

### Concept

A **named blob of data** addressable by a URI like `shlink://health`. The
client lists available resources, the user (or client policy) decides which
to load into context, and the server returns the content on demand. The AI
does **not** "call" a resource the way it calls a tool — the resource lands
in context as readable material.

### When to use it

- The data is **read-only** with no side effects
- The same URI always means "the same kind of thing" (the *content* may
  change, but the *semantics* do not)
- The user/client should decide whether the AI sees it

### FastMCP API

```python
@mcp.resource(
    uri="shlink://health",
    name="Shlink Health Status",
    description="Backend Shlink instance health probe — /rest/health.",
    mime_type="application/json",
)
async def shlink_health() -> dict:
    return await client.health()
```

The function name doesn't matter; the **URI** is the public identifier.

### Status in `bg-shlink-mcp`

**Implemented (5 static resources + 5 templates).** Config-driven via
[`extensions/extensions.json`](../app/bg-shlink-mcp/extensions/extensions.json),
loaded by bg-mcpcore's extensions subsystem. Current catalogue:

| URI | Backend GET |
| --- | --- |
| `shlink://tags` | `/tags` |
| `shlink://tags/stats` | `/tags/stats` |
| `shlink://domains` | `/domains` |
| `shlink://visits/summary` | `/visits` |
| `shlink://visits/orphan` | `/visits/orphan` |

Adding a new resource is a JSON edit — no Python required (see §13 for the
config schema). The synthesised function is wired through `mcp.add_resource(...)`,
which routes static URIs to `FunctionResource` and parameterised URIs to
`ResourceTemplate` automatically.

### Pitfalls

- **Resources are pull, not push.** The client decides when to fetch. Don't
  put rapidly-changing state behind a static resource URI — use a tool or
  emit a `notifications/resources/updated` notification.
- **MIME type matters.** Clients use it to decide how to render. JSON
  surfaces as structured content; `text/markdown` renders inline;
  `image/png` shows as an image. Lying here causes silent UX bugs.
- **Resource ≠ Resource Template.** A resource has a *fixed* URI. The
  moment you need `{shortCode}` in the URI, you need a Template (§3).

---

## 3. Resource Templates — "parameterised file"

### Concept

A URI **pattern** like `shlink://short-url/{shortCode}` that the client
expands on demand. The client lists templates, the user picks one and
provides values, the server fetches the materialised instance.

### When to use it

- You have a **set** of read-only things addressable by a key
- The AI shouldn't have to call a tool just to read one of them
- A picker / autocomplete in the client would be the right UX

### FastMCP API

```python
@mcp.resource("shlink://short-url/{shortCode}")
async def short_url_resource(shortCode: str) -> dict:
    response = await client.get(f"/short-urls/{shortCode}")
    return response.json()
```

FastMCP treats any `@mcp.resource` whose URI contains `{name}` placeholders
as a **resource template** automatically — same decorator, different
detection rule.

### Status in `bg-shlink-mcp`

**Implemented (5 templates).** Layered *next to* the original GET tools, not
*instead of* — both surfaces work. AI clients that don't bind to resources
keep using the tool; clients/users that prefer pin-able URIs get the template.

| Template URI | Backend GET |
| --- | --- |
| `shlink://short-url/{shortCode}` | `/short-urls/{shortCode}` |
| `shlink://short-url/{shortCode}/visits` | `/short-urls/{shortCode}/visits` |
| `shlink://short-url/{shortCode}/redirect-rules` | `/short-urls/{shortCode}/redirect-rules` |
| `shlink://tag/{tag}/visits` | `/tags/{tag}/visits` |
| `shlink://domain/{domain}/visits` | `/domains/{domain}/visits` |

Validation guards against drift: bg-mcpcore's `ResourceConfig` validator
rejects any config where the URI placeholders and the backend-path placeholders
don't line up — so a `{shortCode}` in the URI without a matching `{shortCode}`
in the backend path fails at boot.

### Pitfalls

- **Templates are read-only.** If you need pagination/filtering with a lot
  of optional knobs (e.g. `excludeBots=true&startDate=...`), a tool is
  still the better fit — URI templates don't model rich query semantics.
- **`{name}` matching is strict.** A path with `{name}` *must* be filled —
  no defaults at the URI layer. Provide them at the function signature
  instead.
- **One URI scheme per server.** Pick a scheme prefix (`shlink://`) and
  stick to it. Mixing `shlink://` and `urls://` confuses clients.

---

## 4. Prompts — "a workflow the user selects"

### Concept

A **template the user picks** (typically via a slash-command or picker UI)
that lands in the AI's context as a structured prompt. Prompts do **not**
return data — they return *instructions* that the AI then executes by
calling tools / reading resources.

### When to use it

- A common workflow needs **multiple tool calls in sequence**
- You want to give users a "1-click recipe" without forcing them to
  remember tool names
- The AI shouldn't have to invent the orchestration each time

### FastMCP API

```python
@mcp.prompt(
    name="domain_health_report",
    description="Generate a 30-day click-through summary for one domain.",
)
def domain_health_report(domain: str, days: int = 30) -> str:
    return (
        f"For the domain {domain}, do the following in order:\n"
        f"1. List all short URLs (use list_short_urls with the domain filter).\n"
        f"2. For each short URL, fetch its last {days} days of visits "
        f"(get_short_url_visits with excludeBots=true).\n"
        f"3. Summarise: top 5 by visits, click-through trend, anomalies.\n"
        f"Return a markdown report."
    )
```

Prompts can also return structured `PromptMessage` lists with role
(`user` / `assistant`) for multi-turn priming.

### Status in `bg-shlink-mcp`

**Implemented (1 prompt — `top_visits_report`).** Config-driven via
[`extensions/extensions.json`](../app/bg-shlink-mcp/extensions/extensions.json),
registered by bg-mcpcore's extensions subsystem.

`top_visits_report(limit=20, days=30, exclude_bots=true)` orchestrates
`list_short_urls` → `get_short_url_visits` (per URL) → ranked markdown
table. The Python signature is **synthesised** from the JSON-declared
arguments: a generic closure gets a real `inspect.Signature` attached so
FastMCP introspects the parameter names, types, and defaults the same way
it would for a hand-written `@mcp.prompt`.

Further candidates (not yet wired — easy JSON additions):

| Prompt name | Purpose |
| --- | --- |
| `audit_short_url(shortCode)` | Visits + redirect rules + tags + creation date in one pass |
| `tag_cleanup` | List orphan/low-visit tags, suggest merges or deletions |
| `migrate_long_urls(from_domain, to_domain)` | Find + edit short URLs that point at one domain |

### Pitfalls

- **A prompt is declarative.** Avoid baking in step-by-step instructions
  the AI doesn't need. Trust it to orchestrate; tell it the *goal*, not
  every API call.
- **No state between turns.** Prompts are stateless — each invocation is
  independent. If you need state, store it server-side via a tool.
- **Discoverability is the point.** A prompt the user can't find adds zero
  value. Pair good `name`/`description` with the client's UX
  (Claude Desktop, Inspector, etc. all show prompts in a picker).

---

## 5. Apps — "tool emits a rendered UI"

### Concept

A FastMCP **extension** (`io.modelcontextprotocol/ui`) where a tool or
resource returns HTML/JS/CSS that the client renders in a sandboxed iframe.
The AI's textual answer still happens; the App is an *additional* artefact
the user can interact with directly — charts, form widgets, embedded
viewers.

### When to use it

- Tabular or visual output is a **first-class user need** (a click-through
  chart, a heatmap, a calendar)
- The interaction is **non-conversational** — a date-range picker, a colour
  selector, a filter UI
- The user benefits from seeing the result, not just reading the AI's
  summary

### FastMCP API

```python
from fastmcp.apps.config import AppConfig, ResourceCSP

@mcp.tool(
    name="visits_chart",
    app=AppConfig(
        csp=ResourceCSP(connect_domains=["https://go.bauer-group.com"]),
    ),
)
async def visits_chart(shortCode: str) -> str:
    visits = await client.get(f"/short-urls/{shortCode}/visits")
    return _render_chart_html(visits.json())     # returns text/html
```

Clients that don't support Apps degrade gracefully — they show the raw
output as text. Apps require **per-app CSP** so the iframe can only reach
declared origins; defaults are deny-all.

### Status in `bg-shlink-mcp`

**Not used.** Highest-value candidates: a visits chart and a tag-cloud
view. Both would be HTML-only (no JS) for the simplest CSP posture.

### Pitfalls

- **CSP is non-optional.** Without `connect_domains`, the iframe can't
  reach Shlink. Without `resource_domains`, you can't load fonts/images.
  Start permissive in dev, lock down for prod.
- **No persistent state.** The iframe is recreated each render.
  Server-side persistence is on you.
- **Compatibility floor.** Apps are an extension — Inspector renders them,
  Claude Desktop's support depends on version. Always make the underlying
  tool useful without the App rendering.

---

## 6. Tasks — "long-running tool calls"

### Concept

An **execution mode**, not a separate primitive. A tool can be configured
as task-capable (`task=True` or `task=TaskConfig(...)`); when the AI calls
it, the server returns a `task_id` immediately and the AI polls
`get_tasks` / `get_task_payload` until it's done.

### When to use it

- Backend latency is **>5s typical** (CSV imports, bulk mutations, slow
  third-party APIs)
- The work is **safely interruptible** (failure mid-task doesn't leave
  unrecoverable state)
- Holding the MCP connection open for the duration would block other
  tools

### FastMCP API

```python
@mcp.tool(task=True)                              # opt-in, mode="optional"
async def bulk_import_short_urls(payload: list[dict]) -> dict:
    ...

@mcp.tool(task=TaskConfig(mode="required"))      # never sync
async def long_running_export() -> dict:
    ...
```

`TaskMode` values: `"forbidden"` (default for resources), `"optional"`
(tools can pick at call-time), `"required"` (tasks-only, no sync path).
Inside the function, you can `await ctx.report_progress(...)` to update
the AI.

### Status in `bg-shlink-mcp`

**Implemented (1 task — `export_short_urls`).** Bulk-exports the full
short-URL inventory as CSV or JSON. The procedural logic (pagination +
CSV/JSON rendering) lives in [`src/server.py`](../app/bg-shlink-mcp/src/server.py)
as the profile's `python` tool source (registered via `server:register_export_task`)
and runs as an MCP task through `ctx.request`.

**Why a python source and not an extension:** bg-mcpcore's extensions cover
prompts + resources (pure transformations of config), but a task has procedural
logic (pagination, format conversion) that doesn't compress into config without
ugly DSLs. The profile still controls *which* sources exist; the *how it runs*
is code. (Pre-migration this was a config-driven `Exporter` class in
`extensions/tasks.py`; the export behaviour is byte-for-byte unchanged.)

Adding a new export (e.g. tags, domains as CSV): add a `_fetch_all_*` helper +
a second `register_*_task` in `server.py`, and reference it as another `python`
tool source in the profile. No other code-path touches required.

Further candidates:

- `delete_visits_bulk(date_range=...)` — Shlink's `DELETE /visits` can be
  slow on million-visit instances
- `regenerate_qr_codes(domain=...)` operator helper
- `export_visits(format=csv|jsonl, date_range=...)` — large export with
  streaming output

### Pitfalls

- **Tasks change the AI's reasoning model.** A sync tool returns data
  the AI can immediately reason on; a task returns "I'll let you know" —
  the AI must remember to poll. For short waits, sync is simpler.
- **Polling cadence.** `DEFAULT_POLL_INTERVAL` is 5s; tune
  `TaskConfig(poll_interval=...)` based on the operation.
- **TTL is a contract.** `DEFAULT_TTL_MS = 60_000`. If the task can run
  longer, raise it explicitly — otherwise the result is gc'd before the
  client retrieves it.

---

## 7. Ping — "are you still there?"

### Concept

A no-op JSON-RPC request the client (or server) sends to test liveness.
Handled at the transport layer; no application code involved.

### When to care about it

Almost never directly. Pings matter when:

- A reverse proxy idles out long-lived `streamable-http` connections — set
  the proxy's idle timeout higher than the client's ping interval
- You're debugging a "server unresponsive" report — the Inspector's Ping
  tab confirms the transport is healthy independent of any tool

### FastMCP / MCP API

Automatic. The client sends `{"method": "ping"}`, the server replies
empty. There's no decorator to register. FastMCP's transport layer
handles it; observability lives in the server's request logs.

### Status in `bg-shlink-mcp`

**Automatic.** Streamable-HTTP transport handles it. Our
[`/healthz`](../app/bg-shlink-mcp/src/server.py) endpoint is a separate,
HTTP-level liveness probe used by Docker `HEALTHCHECK` — **not** the same
as MCP ping. Don't confuse them:

| Endpoint | Audience | Behaviour |
| --- | --- | --- |
| `GET /healthz` | Docker / k8s | 200 OK once the FastMCP lifespan reports ready |
| MCP `ping` | AI client | JSON-RPC round-trip on the open MCP session |

### Pitfalls

- **Reverse-proxy idle timeouts.** Cloudflare defaults to 100s, Traefik to
  60s. Pings keep the connection warm — but only if the proxy actually
  treats the keepalive traffic as activity. Test with `streamable-http` +
  a long pause.
- **Don't add `@mcp.tool def ping(): pass`.** That creates a *tool* named
  ping, not the protocol-level ping. They're unrelated.

---

## 8. Sampling — "server asks the AI for a completion"

### Concept

Direction reversal: usually the AI calls the server's tools, but with
sampling, **a tool calls the AI's model** for help — summarisation,
classification, structured extraction. The client decides whether to
allow it (and on which model).

### When to use it

- The tool needs **natural-language reasoning** mid-execution (classify
  user-supplied text, summarise an article fetched from a URL, extract
  entities)
- Hard-coding rules would be brittle
- You'd otherwise need a separate LLM API key on the server — sampling
  reuses the client's existing model access

### FastMCP API

```python
from fastmcp import Context

@mcp.tool
async def categorise_short_url(longUrl: str, ctx: Context) -> str:
    article = await fetch(longUrl)
    result = await ctx.sample(
        messages=[{"role": "user", "content":
            f"Categorise this article in one word:\n\n{article[:2000]}"}],
        max_tokens=10,
        model_preferences={"hints": [{"name": "haiku"}]},   # cheap model
    )
    return result.content[0].text.strip()
```

### Status in `bg-shlink-mcp`

**Not used.** No tool currently needs LLM-style reasoning to compute its
result — every operation is a deterministic Shlink REST call. Future
candidates:

- `auto_tag_short_url(longUrl)` — fetch the page, ask the model for 3
  topic tags, set them on the short URL
- `summarise_visit_patterns(shortCode)` — instead of returning raw visit
  data, return an LLM-generated weekly summary

### Pitfalls

- **The client controls the model.** You can hint (`model_preferences`),
  not dictate. Clients may refuse sampling entirely (no `sampling`
  capability advertised) — always handle `ctx.client_supports_extension`
  gracefully.
- **Cost & latency lives on the user's bill.** Sampling consumes the
  client's token budget. Don't sample inside a hot loop.
- **No nested sampling without care.** A sampled response can include
  further tool calls. Plan the depth budget.

---

## 9. Elicitations — "server asks the user for input"

### Concept

A tool can pause and ask the user a question — typed input, a choice from
options, a confirmation. The client surfaces a UI prompt and returns the
answer to the server, which resumes the tool.

### When to use it

- Required info is **missing from the tool arguments** and the AI can't
  reasonably guess (an OTP, a confirmation of a destructive action, a
  custom slug preference)
- You want a **human-in-the-loop gate** stronger than `destructiveHint`
- Multi-step workflows where step N's answer determines step N+1's input

### FastMCP API

```python
@mcp.tool
async def delete_short_url_with_confirm(shortCode: str, ctx: Context) -> dict:
    confirmation = await ctx.elicit(
        message=f"Permanently delete {shortCode}? Visit history is lost.",
        schema={"type": "object", "properties": {
            "confirm": {"type": "boolean"}
        }, "required": ["confirm"]},
    )
    if not confirmation.content.get("confirm"):
        return {"cancelled": True}
    return await client.delete(f"/short-urls/{shortCode}")
```

### Status in `bg-shlink-mcp`

**Not used.** All destructive operations rely on client-side annotation
gating (`destructiveHint=true`). Elicitations would add a stronger
*server-enforced* confirmation that survives even if a client ignores
annotations.

### Pitfalls

- **Capability negotiation.** Like sampling, elicitation requires client
  support. Check `ctx.client_supports_extension("io.modelcontextprotocol/elicitation")`
  and provide a fallback.
- **UX friction.** Every elicitation interrupts the AI's flow. Use
  sparingly — only when the answer truly *cannot* come from the AI.
- **Don't elicit secrets.** Use OAuth scopes or a separate input channel.
  Elicitations are not designed for high-sensitivity input.

---

## 10. Roots — "the filesystem scope the user granted"

### Concept

The client (Claude Desktop, Cursor, …) advertises a list of **filesystem
roots** the user has granted the server access to. A server tool can
`await ctx.list_roots()` to discover them and operate within those
boundaries — read files, scan directories, write outputs.

### When to use it

- The server's value depends on **user-local files** (code analyzers,
  doc indexers, image annotators)
- You're building a desktop-first MCP, not a hosted one

### FastMCP API

```python
@mcp.tool
async def index_local_links(ctx: Context) -> list[str]:
    roots = await ctx.list_roots()
    found: list[str] = []
    for root in roots:
        for path in Path(root.uri).rglob("*.md"):
            found.extend(extract_short_urls(path.read_text()))
    return found
```

### Status in `bg-shlink-mcp`

**Not used and not applicable.** `bg-shlink-mcp` is a **remote** MCP
talking to a remote Shlink — there is no filesystem-roots story here.
Streamable-HTTP transport over the internet means there is no shared
filesystem with the client. This tab will always be empty in our
Inspector.

### Pitfalls

- **Roots only exist on stdio/desktop transports.** Streamable-HTTP
  clients won't advertise roots — `list_roots()` returns `[]`.
- **Roots are advisory.** The user may revoke at any time; treat them
  as a snapshot, not a long-lived contract.
- **Path traversal still your problem.** A root grants *access to* a
  tree, not *permission for* every operation under it. Validate paths
  the same way you would for any user-supplied input.

---

## 11. Auth — "who is allowed to do what"

### Concept

Two independent layers:

1. **Transport auth** — who can open an MCP session at all? Inbound
   OAuth 2.1 with the AI client acting as the OAuth client.
2. **Per-component auth** — within an authenticated session, which tools /
   resources / prompts is *this* identity allowed to invoke?

### FastMCP API

```python
# Transport layer (in server.py)
from fastmcp.server.auth import AzureProvider
mcp = FastMCP(name="…", auth=AzureProvider(...))

# Per-tool (later, when scopes get fine-grained)
from fastmcp.server.auth import AuthCheck

@mcp.tool(auth=AuthCheck(scopes=["shlink.write"]))
async def create_short_url(...): ...

@mcp.tool(auth=AuthCheck(scopes=["shlink.read"]))
async def list_short_urls(...): ...
```

### Status in `bg-shlink-mcp`

**Transport layer fully implemented.** Inbound OAuth 2.1 supports Entra
(single + multi tenant), Google Workspace, generic OIDC, and a
development-only `none` mode — see
[docs/authentication.md](authentication.md).

**Per-component layer not yet used.** All tools currently inherit the
session's identity with no further scope check. Three reasons we
haven't (yet) split scopes per tool:

- Shlink's API key has no read-only mode anyway — once auth'd, the
  server can do everything
- The downstream auth subject is still "the server", not the user
- BAUER GROUP deployments today gate identity at the IdP, not per-tool

When to revisit:

- Multi-user deployments where some users should only read
- Compliance regimes requiring per-tool audit trails of *which scope was used*

### Pitfalls

- **Inbound and outbound are different boundaries.** The OIDC token
  *never* reaches Shlink ([authentication.md:8-11](authentication.md#L8-L11)).
  Don't blur the layers — it's a security property.
- **`AUTH_MODE=none` is dev-only.** Enforced by Pydantic when
  `ENVIRONMENT≠development`. Don't bypass that check.
- **Annotations ≠ Auth.** A `readOnlyHint=true` annotation tells the
  *client* the call is safe; it does **not** prevent the server from
  doing anything. Auth is what actually enforces.

---

## 12. Metadata — "everything else"

### Concept

The protocol-defined `_meta` field on every component (tools, resources,
prompts) plus FastMCP-specific fields like `annotations`, `tags`, `icons`,
`version`. Used for:

- Hints that don't fit the standardised schema (custom client extensions)
- Tagging for filtering / catalogue rendering
- Versioning a tool's contract over time
- Icons for UI rendering

### FastMCP API

```python
@mcp.tool(
    name="create_short_url",
    version="2.0",                        # surfaces to clients that show versions
    title="Create Short URL",             # human-readable name
    tags={"shlink", "write"},             # filter / catalogue keys
    icons=[Icon(src="https://…/icon.svg", mimeType="image/svg+xml")],
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False,
        openWorldHint=True,
    ),
    meta={                                # arbitrary, namespaced
        "io.bauer-group/cost-tier": "free",
        "io.bauer-group/sla-target-ms": 500,
    },
)
async def create_short_url(...): ...
```

### Status in `bg-shlink-mcp`

**Partially implemented.**

| Field | Usage |
| --- | --- |
| `annotations` | All 25 OpenAPI tools, via the profile's `annotations: by_http_method` (bg-mcpcore applies the per-HTTP-method hints) |
| `tags` | Spec-derived (from each operation's OpenAPI `tags`). The pre-migration uniform `{"shlink"}` tag is no longer applied — it was redundant metadata on a single-purpose server |
| `description` | Spec-derived + 3 overrides from the profile's `descriptions` map |
| `version` | Not set per-tool (the server has one image version; tools inherit) |
| `icons` | Not set |
| `_meta` | Not set |
| `title` | Not set (clients display the `name`) |

### Pitfalls

- **`_meta` keys must be namespaced.** `{"io.bauer-group/foo": ...}`, not
  `{"foo": ...}`. The protocol reserves un-namespaced keys for itself.
- **Bumping `version` is a contract change.** Clients may pin to a
  version. Don't bump silently when the schema changes.
- **Tags are filter keys, not human-facing.** Use `title` /
  `description` for what the user reads; use `tags` for what the
  catalogue / search filters on.

---

## 13. The `extensions/extensions.json` config — schema reference

All operator-defined prompts, resources, and tasks live in one JSON file
baked into the image at `/app/extensions/extensions.json` (override via
`EXTENSIONS_CONFIG_PATH`). bg-mcpcore's extensions Pydantic models are the
source of truth — they enforce `extra=forbid`, so typos in keys fail at boot
rather than silently dropping an entry. (Note: bg-mcpcore extensions cover
prompts + resources only; the export task is a `python` tool source — see §4.)

### Top-level shape

```json
{
  "$schema": "./extensions.schema.json",
  "prompts":   [ /* PromptConfig */ ],
  "resources": [ /* ResourceConfig */ ],
  "tasks":     [ /* ExportTaskConfig */ ]
}
```

Every section is optional and may be empty.

### Prompt entry

```json
{
  "name": "snake_case_identifier",
  "title": "Human-Readable Name",
  "description": "What the user sees in the picker.",
  "tags": ["analytics"],
  "arguments": [
    {
      "name": "limit",
      "type": "integer",         // string | integer | number | boolean
      "description": "What this argument means.",
      "default": 20,             // optional; must match `type`
      "required": false
    }
  ],
  "template": "Top ${limit} results …"   // ${name} placeholders only
}
```

Validation: every `${placeholder}` in the template must have a declared
argument, every declared argument must appear in the template — config
drift fails at boot.

### Resource entry

```json
{
  "uri": "shlink://short-url/{shortCode}",   // {placeholder} → template
  "name": "Short URL Details",
  "title": "Short URL",
  "description": "…",
  "mime_type": "application/json",
  "tags": ["catalogue"],
  "backend": {
    "method": "GET",                          // only GET makes sense
    "path": "/short-urls/{shortCode}"        // Shlink-relative path
  }
}
```

Validation: URI `{placeholder}`s and backend-path `{placeholder}`s must
match exactly.

### Task entry

```json
{
  "name": "export_short_urls",
  "title": "Export Short URLs",
  "description": "…",
  "tags": ["export"],
  "exporter": "short_urls",                  // key into _EXPORTERS in tasks.py
  "formats": ["csv", "json"],                // first entry is the default
  "page_size": 200,
  "task": {
    "mode": "required",                       // optional | required
    "poll_interval_seconds": 2.0
  }
}
```

Validation: `exporter` must reference a class registered in
`_EXPORTERS` (Pydantic `Literal["short_urls"]`); `formats` must be
non-empty and unique.

**`mode` semantics:**

- `"optional"` — clients pick per call (`call_tool(..., task=True|False)`).
  Default is sync; the Tasks tab stays empty unless the client opts in.
- `"required"` — sync mode is rejected; every call goes through the task
  queue and surfaces in the Tasks tab. The right choice for bulk
  operations whose result shouldn't pollute the LLM's context window.

**Why no `ttl_seconds`:** FastMCP's `TaskConfig` only exposes `mode` and
`poll_interval`. The task TTL is a per-call client knob
(`call_tool(..., ttl=...)`, default 60s) with no server-side default
override. Exposing it in operator config would be cargo-cult — it would
silently do nothing.

---

## Implementation roadmap for `bg-shlink-mcp`

Sorted by value-to-effort ratio. Updated as features land.

### Done

- ✅ **Spec-derived tool surface** — 25 tools from Shlink's OpenAPI
- ✅ **Resource Templates** for read-only GETs (5 templates layered next
  to the original tools)
- ✅ **Static Resources** for catalogue endpoints (4 — tags, domains,
  visits summary, orphan visits)
- ✅ **Prompt — `top_visits_report`** (multi-tool workflow)
- ✅ **Task — `export_short_urls`** (CSV/JSON bulk export with paginated
  fetcher)
- ✅ **OAuth 2.1 inbound auth** at the transport layer
- ✅ **Per-method annotations** (`readOnlyHint` / `destructiveHint`)

### Now (low risk, clear value)

- **More prompts.** `audit_short_url(shortCode)`, `tag_cleanup`,
  `migrate_long_urls(from_domain, to_domain)`. Each is a JSON edit; no
  Python required.
- **Resource for health.** Add a resource entry to `extensions.json`
  (`uri: "shlink://health"`, `backend.method: GET`, `backend.path: /health`) so
  the catalogue exposes upstream health as a read-only resource — a JSON edit,
  no Python required.

### Soon (clear value, more design surface)

- **Elicitation for destructive operations.** Wrap `delete_short_url`,
  `delete_tags`, `rename_tag` in an elicitation confirm step — stronger
  guarantee than `destructiveHint`.
- **More exporters.** `export_tags`, `export_domains`, `export_visits` —
  each one is a new `Exporter` subclass + a JSON entry.

### Later (operator value, advanced)

- **Apps** for click-through charts and tag-cloud visualisations.
- **Per-tool Auth scopes** (`shlink.read`, `shlink.write`, `shlink.delete`)
  — only when there's a multi-user deployment that needs them.

### Probably never

- **Sampling** — every Shlink operation is deterministic. Sampling is a
  bad fit unless we add an `auto_tag_short_url` style feature that reasons
  about page content.
- **Roots** — remote MCP, no shared filesystem with the client.

---

## Quick reference card

```text
                ┌─────────────────────────────────────────────────────┐
                │                MCP Component Primitives             │
                │                                                     │
                │   AI calls            User picks       User reads   │
                │   ┌──────┐         ┌──────────┐     ┌────────────┐  │
                │   │ Tool │         │ Prompt   │     │ Resource   │  │
                │   └──────┘         └──────────┘     └──────┬─────┘  │
                │       │                  │                 │        │
                │   side effects        workflow         parameterise │
                │   schema args         template              │       │
                │   annotations           │            ┌──────┴────┐  │
                │                         │            │ Resource  │  │
                │                         │            │ Template  │  │
                │                         │            └───────────┘  │
                │                         │                           │
                │                  (Apps extend either Tools or       │
                │                   Resources with rendered UI)       │
                └─────────────────────────────────────────────────────┘

         ┌────────────────────┐         ┌──────────────────────────┐
         │ Execution modes    │         │ Server→Client capabilities│
         │ ─ Tasks (long-run) │         │ ─ Sampling (LLM-help)    │
         └────────────────────┘         │ ─ Elicitations (user-in) │
                                        │ ─ Roots (fs scope)       │
                                        └──────────────────────────┘

         ┌────────────────────────────────────────────────────────┐
         │ Plumbing                                               │
         │ ─ Ping     (transport keepalive — automatic)           │
         │ ─ Auth     (transport identity + per-component scopes) │
         │ ─ Metadata (_meta, annotations, tags, icons, version)  │
         └────────────────────────────────────────────────────────┘
```

---

## Related reading

- [docs/installation.md](installation.md) — how this server is deployed
- [docs/authentication.md](authentication.md) — Auth in depth
- [docs/testing.md](testing.md) — MCP Inspector workflow for verifying any
  of the above
- [docs/troubleshooting.md](troubleshooting.md) — what to do when a
  primitive doesn't behave
- [profiles/shlink.json](../app/bg-shlink-mcp/src/profiles/shlink.json) —
  the declarative Tools/Resources/auth wiring (executed by bg-mcpcore)
- [MCP specification](https://spec.modelcontextprotocol.io) — protocol
  source of truth
- [FastMCP 3.x docs](https://gofastmcp.com) — Python API reference
