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
| **Tools** | Component primitive | AI in the turn | **implemented** (25) | The AI calls them to *do* something |
| **Resources** | Component primitive | User / client pins them | **not used** (0) | The AI *reads* them like files |
| **Resource Templates** | Component primitive | User picks a URI | **not used** (0) | Parameterised resources (`shlink://short-url/{shortCode}`) |
| **Prompts** | Component primitive | User picks a workflow | **not used** (0) | Pre-baked multi-tool workflows (slash-command-like) |
| **Apps** | UI extension on a component | Client renders | **not used** (0) | Tool/resource emits an HTML/UI snippet, client renders sandboxed |
| **Tasks** | Execution mode | AI opts in | **not used** | Long-running tool calls — return task-ID, poll later |
| **Ping** | Protocol plumbing | Either side | **automatic** | Keepalive — no app code needed |
| **Sampling** | Server→client capability | Server, inside a tool | **not used** | Server asks the client's LLM for a completion |
| **Elicitations** | Server→client capability | Server, inside a tool | **not used** | Server asks the user for missing input |
| **Roots** | Client→server capability | Client advertises | **not used** | Filesystem scopes the client has granted to the server |
| **Auth** | Cross-cutting | Transport + per-component | **implemented** (OAuth 2.1) | Inbound identity gate + per-tool scope checks |
| **Metadata** | Cross-cutting | Any component | **implemented** (annotations) | `_meta` / annotations carry hints the protocol doesn't standardise |

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

We do not hand-write tool functions. `FastMCP.from_openapi()` walks the
spec and registers every operation as a tool — see
[tool_mapper.py:298-340](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L298-L340).
The same module applies four post-processing passes:

| Pass | What it does | Where |
| --- | --- | --- |
| `_normalize_spec_for_v3` | Strips `/rest/v{version}` from paths and drops the `version` path-param so the LLM never sees it | [tool_mapper.py:140-183](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L140-L183) |
| `SHLINK_ROUTE_MAPS` | Filters routes — `/mercure-info` excluded, `/health` becomes a Resource | [tool_mapper.py:56-69](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L56-L69) |
| `NAME_OVERRIDES` | Maps PascalCase `operationId`s to snake_case (`createShortUrl` → `create_short_url`) | [tool_mapper.py:77-106](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L77-L106) |
| `METHOD_ANNOTATIONS` | Per-HTTP-method safety hints applied via `mcp_component_fn` | [tool_mapper.py:44-50](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L44-L50) |

### Pitfalls

- **Annotations are hints, not authorisation.** Clients may ignore
  `destructiveHint`. Treat them as defence-in-depth; keep the Shlink API key
  on a need-to-know basis regardless.
- **A "useful description" doubles the AI's hit rate.** Shlink's spec
  summaries are sometimes uninformative ("List short URLs"). Hand-written
  `DESCRIPTION_OVERRIDES` ([tool_mapper.py:111-124](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L111-L124))
  exist for exactly this reason on the three most-used tools.
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

**Not used (`resource_count=0`).** The route map declares `^/health$` →
`MCPType.RESOURCE`, but Shlink's actual health path is `/rest/health` (not
`/rest/v{version}/health`), so the regex never matches and the operation
falls through to a tool. Three viable fixes:

1. Add `^/rest/health$` to the route maps (one-liner)
2. Hand-write a `@mcp.resource("shlink://health")` that calls
   `ShlinkClient.health()` directly — bypasses the OpenAPI mapper
3. Leave it as a tool — works fine, just less semantically pure

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

**Not used.** Strong candidates for migration from tools to templates:

| Current tool | Suggested template URI |
| --- | --- |
| `get_short_url(shortCode)` | `shlink://short-url/{shortCode}` |
| `get_short_url_visits(shortCode)` | `shlink://short-url/{shortCode}/visits` |
| `get_tag_visits(tag)` | `shlink://tag/{tag}/visits` |
| `get_domain_visits(domain)` | `shlink://domain/{domain}/visits` |

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

**Not used.** Good candidates:

| Prompt name | Purpose |
| --- | --- |
| `weekly_link_report` | Top short URLs by visits + new-this-week |
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

**Not used.** Shlink answers in <500ms for all current operations, so
every tool runs synchronously. Hypothetical future candidates:

- `delete_visits_bulk(date_range=...)` — Shlink's `DELETE /visits` can be
  slow on million-visit instances
- A future `regenerate_qr_codes(domain=...)` operator helper

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
| `annotations` | All 25 tools, via `METHOD_ANNOTATIONS` ([tool_mapper.py:44-50](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L44-L50)) |
| `tags` | `{"shlink"}` on all generated tools ([tool_mapper.py:325](../app/bg-shlink-mcp/src/shlink/tool_mapper.py#L325)) |
| `description` | Spec-derived + 3 manual overrides |
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

## Implementation roadmap for `bg-shlink-mcp`

Sorted by value-to-effort ratio.

### Now (low risk, immediate user value)

1. **Resource for health.** Add `^/rest/health$` to `SHLINK_ROUTE_MAPS`
   or hand-write a `@mcp.resource("shlink://health")`. Surfaces the
   health probe as a pin-able resource in clients that support it.
2. **Resource Templates for read-only GETs.** Migrate
   `get_short_url`, `get_short_url_visits`, `get_tag_visits`,
   `get_domain_visits` from tools to templates. ~10 lines per
   migration; halves the LLM's "should I call a tool?" decisions.

### Soon (clear user value, more design surface)

3. **Two or three Prompts** for the most common multi-tool workflows:
   - `weekly_link_report` — top short URLs + new this week
   - `audit_short_url(shortCode)` — visits + redirect rules + tags in one
   - `migrate_long_urls(from_domain, to_domain)` — bulk re-target
4. **Elicitation for destructive operations.** Wrap `delete_short_url`,
   `delete_tags`, `rename_tag` in an elicitation confirm step — stronger
   guarantee than `destructiveHint`.

### Later (operator value, advanced)

5. **Apps** for click-through charts and tag-cloud visualisations.
6. **Per-tool Auth** scopes (`shlink.read`, `shlink.write`,
   `shlink.delete`) — only when there's a multi-user deployment that
   needs them.
7. **Tasks** for any future bulk-mutation tool (`delete_visits_bulk`,
   `regenerate_qr_codes`).

### Probably never

8. **Sampling** — every Shlink operation is deterministic. Sampling is a
   bad fit unless we add an `auto_tag_short_url` style feature that
   reasons about page content.
9. **Roots** — remote MCP, no shared filesystem with the client.

---

## Quick reference card

```
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
- [tool_mapper.py](../app/bg-shlink-mcp/src/shlink/tool_mapper.py) —
  the actual Tools/Resources wiring
- [MCP specification](https://spec.modelcontextprotocol.io) — protocol
  source of truth
- [FastMCP 3.x docs](https://gofastmcp.com) — Python API reference
