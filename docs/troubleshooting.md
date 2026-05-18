# Troubleshooting

Common deployment and runtime issues, grouped by where they show up.

---

## Startup

### `ValueError: SHLINK_API_KEY is required and must not contain the CHANGE_ME placeholder`

`.env` still has `SHLINK_API_KEY=CHANGE_ME_SHLINK_API_KEY`. Generate a real
key inside the Shlink container and paste it in:

```bash
docker exec shlink shlink api-key:generate --name="mcp-server"
```

### `ValueError: AUTH_MODE=none is forbidden in production`

`ENVIRONMENT=production` plus `AUTH_MODE=none` is not allowed by Pydantic.
Either pick a real `AUTH_MODE`, or set `ENVIRONMENT=development` if you
truly want an unauthenticated MCP (never expose it to the network in that
state).

### `ValueError: AUTH_STORAGE_ENCRYPTION_KEY is required when AUTH_REDIS_URL is set`

Redis-backed client storage requires Fernet at-rest encryption. Generate
once:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Paste into `.env` as `AUTH_STORAGE_ENCRYPTION_KEY=`.

### `ValueError: AzureProvider requires at least one non-OIDC scope in required_scopes`

FastMCP 3.x refuses to start if `ENTRA_API_SCOPES` is empty or contains only OIDC
scopes. Custom API scopes (e.g. `access_as_user`) must exist in the Azure app
registration under "Expose an API" — OIDC scopes never appear in Azure access
token `scp` claims and can't be enforced. Two fixes:

- **If the scope is missing in Azure:** add one via `scripts/setup-azure-app.ps1`
  / `.sh` or follow [docs/authentication.md → Manual setup → step 3](authentication.md#3-expose-an-api).
- **If the scope exists but you renamed it:** set `ENTRA_API_SCOPES=<your-name>`
  in `.env` (default is `access_as_user`).

### Container starts but `/healthz` returns 5xx

Check the boot logs:
```bash
docker compose logs -f shlink-mcp
```

Common causes:
- **Shlink unreachable** — the initial `OpenAPI spec` load fails. Verify
  `SHLINK_URL` is correct from inside the container:
  `docker exec bg-shlink-mcp curl -fsS $SHLINK_URL/rest/v3/health`
- **OIDC discovery URL unreachable** — only with `AUTH_MODE=oidc` and
  `OIDC_DISCOVERY_URL` set. The provider builder fetches it at startup.

---

## OAuth flow

### "Reply URL does not match" / `AADSTS50011`

The redirect URI registered in your IdP doesn't match what FastMCP sends. The
exact values:

| `AUTH_MODE` | Redirect URI |
| --- | --- |
| `entra-single` / `entra-multi` | `${PUBLIC_BASE_URL}/auth/callback` |
| `google` | `${PUBLIC_BASE_URL}/auth/callback` |
| `oidc` | `${PUBLIC_BASE_URL}/auth/callback` |

FastMCP's `OAuthProxy` uses a single shared redirect path for every upstream
provider — there is no `/auth/azure/`, `/auth/google/` or `/auth/oidc/` sub-path.

Watch out for:
- Case mismatch (some IdPs are case-sensitive)
- Trailing slash mismatch
- `http://` vs `https://`
- Missing port (`:8000` accidentally included or excluded)

### `invalid_client` from Entra

`ENTRA_CLIENT_SECRET` is set to the **secret ID** instead of the **value**.
Azure shows both but only displays the *value* once at creation time. Create a
fresh secret and copy the value column.

### `400 Bad Request: invalid_scope`

The IdP doesn't recognise a scope being requested at authorization time. Per
provider:

- **Entra:** check `ENTRA_API_SCOPES` — the value must match a scope you've
  exposed under "Expose an API" in the app registration (default
  `access_as_user`). Then check `ENTRA_EXTRA_SCOPES` for typos in Graph
  delegated permissions (e.g. `User.Read`, `Mail.Read`).
- **Generic OIDC:** check `OIDC_SCOPES`. Trim to `openid profile email` first,
  then add scopes incrementally.

### `401 insufficient_scope` after successful login

Token validation rejected the access token. Almost always: the Azure app
registration's manifest still has `accessTokenAcceptedVersion` (or
`requestedAccessTokenVersion`) set to `null` or `1`. Legacy v1 tokens carry
their scope in `roles`, not `scp` — FastMCP only reads `scp`. Set the manifest
property to `2` and re-authenticate.

### Tokens stop working after every redeploy

`AUTH_JWT_SIGNING_KEY` changed. Set it once and treat it like a long-lived
secret (1Password vault item). Same for `AUTH_STORAGE_ENCRYPTION_KEY`.

---

## Shlink calls

### `ShlinkAuthError: HTTP 401`

The MCP-side API key was rejected by Shlink. Possible causes:

- The key was revoked in Shlink (`docker exec shlink shlink api-key:list`)
- A typo in `.env` — copy-paste it from the Shlink command output verbatim
- The key was generated against a *different* Shlink instance

### `ShlinkNotFound: HTTP 404` on `GET /short-urls/<code>`

The short code doesn't exist on this Shlink instance, or `SHLINK_URL` points
at the wrong instance. Verify:

```bash
docker exec bg-shlink-mcp curl -fsS \
  -H "X-Api-Key: ${SHLINK_API_KEY}" \
  ${SHLINK_URL}/rest/v3/short-urls?itemsPerPage=1
```

### `ShlinkValidationError: longUrl ...`

The LLM produced an invalid request body. The error detail is forwarded as-is.
If recurring, tighten the tool description in
`app/bg-shlink-mcp/src/shlink/tool_mapper.py` (`DESCRIPTION_OVERRIDES`).

### Periodic spec-refresh warning

```
openapi.refresh_failed source=... error=...
```

You only see this if you've opted into runtime refresh (`SHLINK_OPENAPI_REFRESH_INTERVAL > 0`)
AND set `SHLINK_OPENAPI_URL` to a remote source. The cached spec stays in
place — tools keep working until the next successful fetch.

The default deployment doesn't refresh at all: the spec is baked into the
image at build time (`file:///app/openapi/shlink.json`), so there is no
remote endpoint to be flaky. If you've turned on refresh and the warnings
keep coming, switch back to the baked-in default (unset both env vars) or
point at a stable in-house mirror.

### Wrong tool surface after a Shlink upgrade

The baked-in spec is pinned at image-build time. If your Shlink instance
runs a newer version than the image was built against, new endpoints won't
appear as MCP tools and removed endpoints will still be listed.

Two fixes, pick the one that matches your update cadence:

1. **Rebuild the image with the matching version** (recommended for pinned
   stacks):

   ```bash
   docker build --build-arg SHLINK_OPENAPI_VERSION=v5.1.0 -t bg-shlink-mcp .
   ```

2. **Track upstream at runtime** (no rebuild needed):

   ```ini
   SHLINK_OPENAPI_URL=https://raw.githubusercontent.com/shlinkio/shlink/v5.1.0/docs/swagger/swagger.json
   SHLINK_OPENAPI_REFRESH_INTERVAL=3600
   ```

---

## Reverse proxy

### Traefik returns 404 for `/.well-known/oauth-protected-resource`

The `app` router rule is missing the path. The included compose file uses
`Host(...)` only, which routes *every* path on the host through. If you've
overridden the rule, ensure it covers `/.well-known/*` and `/mcp`.

### Streaming responses get buffered (clients hang)

Traefik defaults are fine for streamable-http (POST + chunked transfer). Do
**not** add `forwardedHeaders` or `buffering` middlewares — they break the
event-stream framing FastMCP uses.

### Coolify 502 Bad Gateway

The application port mismatch. The compose file `expose`s `8000`; Coolify
auto-detects this. If you changed `MCP_PORT`, update the `expose:` block
too.

---

## Diagnostics

### Enable debug logging

```ini
LOG_LEVEL=DEBUG
LOG_FORMAT=console
```

Rich-coloured output, useful locally. In prod keep `LOG_FORMAT=json`.

### Probe Shlink from inside the container

```bash
docker exec bg-shlink-mcp python src/main.py health
```

Exits 0 if Shlink replies on `/health`, 1 otherwise.

### Inspect the live tool catalogue

```bash
docker exec bg-shlink-mcp python src/main.py tools
```

Prints the markdown catalogue to stdout — same content as `docs/tools.md`.
Useful to confirm which tools your Shlink version exposes.

### Sentry

If `SENTRY_DSN` is set, all exceptions are forwarded with stack traces.
`SENTRY_TRACES_SAMPLE_RATE` defaults to `0.05` (5%). For incident
investigation, bump it temporarily to `1.0` and redeploy.

---

## Still stuck?

Open an issue with:
- The output of `docker compose -f docker-compose.<flavour>.yml ps`
- The last 50 log lines from `docker compose logs shlink-mcp`
- The response of `curl -fsS https://<host>/.well-known/oauth-protected-resource`
- Your `AUTH_MODE` and a sanitised version of the matching provider block

https://github.com/bauer-group/IP-Shlink-MCPServer/issues
