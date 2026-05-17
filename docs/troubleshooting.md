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
| `entra-single` / `entra-multi` | `${PUBLIC_BASE_URL}/auth/azure/callback` |
| `google` | `${PUBLIC_BASE_URL}/auth/google/callback` |
| `oidc` | `${PUBLIC_BASE_URL}/auth/oidc/callback` |

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

Your IdP doesn't recognise one of the scopes in `ENTRA_EXTRA_SCOPES` or
`OIDC_SCOPES`. Trim down to just `openid profile email offline_access` first,
then add scopes incrementally.

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

Shlink's spec endpoint is temporarily unreachable. The cached spec stays in
place — tools keep working. If failures persist, switch to a pinned local
spec:

```ini
SHLINK_OPENAPI_URL=file:///app/openapi/shlink-v5.0.1.json
```

…and mount the file via a volume.

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
