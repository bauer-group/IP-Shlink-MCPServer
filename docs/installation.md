# Installation

Three supported deployment flavours, one Docker image.

| Flavour | When to use | Compose file |
| --- | --- | --- |
| Development | Local laptop, no proxy, optional `AUTH_MODE=none` | `docker-compose.development.yml` |
| Traefik | Self-hosted host with existing Traefik reverse proxy | `docker-compose.traefik.yml` |
| Coolify | PaaS deployment via the Coolify dashboard | `docker-compose.coolify.yml` |

---

## Prerequisites

- Docker Engine 24+ (Compose v2)
- A reachable Shlink instance — same Docker network or public URL
- A Shlink API key generated inside the Shlink container:
  ```bash
  docker exec shlink shlink api-key:generate --name="mcp-server"
  ```
- One OAuth/OIDC provider configured *or* a willingness to run in dev mode

---

## 1 · Generate the .env file

```bash
git clone https://github.com/bauer-group/IP-Shlink-MCPServer.git
cd IP-Shlink-MCPServer
cp .env.example .env
python scripts/generate-env.py --force
```

`generate-env.py` writes secure random values for:

- `AUTH_JWT_SIGNING_KEY` (32-byte hex)
- `AUTH_STORAGE_ENCRYPTION_KEY` (Fernet key — used by the Redis-backed store)

You must still fill in by hand:

- `SHLINK_URL`
- `SHLINK_API_KEY` (from the Shlink container, see above)
- `AUTH_MODE` + the matching provider block — see [authentication.md](authentication.md)
- `PUBLIC_BASE_URL` (production) / `SHLINK_MCP_HOSTNAME` (Traefik)

---

## 2a · Development

Lowest-friction option for local exploration.

```bash
docker compose -f docker-compose.development.yml up -d --build
docker compose -f docker-compose.development.yml logs -f shlink-mcp
```

The container exposes port `${SHLINK_MCP_PORT}` (default `8000`) directly.
You can test the server with:

```bash
curl -fsS http://localhost:8000/healthz
# {"status":"ok"}
```

In dev mode, `AUTH_MODE=none` is permitted (a loud red warning is logged at
boot). Use this **only** for local CLI testing with
[mcp-inspector](https://github.com/modelcontextprotocol/inspector) — never for
anything exposed to the network.

---

## 2b · Production · Traefik

Assumes you already run Traefik with HTTPS termination on a Docker network
named `${PROXY_NETWORK}` (default `EDGEPROXY`).

### DNS preflight

```bash
dig +short ${SHLINK_MCP_HOSTNAME}  # must resolve to this host's public IP
```

### Bring up

```bash
docker compose -f docker-compose.traefik.yml up -d
```

### Verify

```bash
# RFC 9728 protected-resource metadata
curl -fsS https://${SHLINK_MCP_HOSTNAME}/.well-known/oauth-protected-resource | jq

# Unauthenticated POST -> 401 with WWW-Authenticate
curl -i -X POST https://${SHLINK_MCP_HOSTNAME}/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expected response:
```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://.../.well-known/oauth-protected-resource"
```

---

## 2c · Production · Coolify

Coolify owns ingress + TLS via its own Traefik instance. Steps:

1. **Create a new application** in the Coolify dashboard.
2. **Source**: this repository, branch `main`.
3. **Build pack**: Docker Compose.
4. **Compose file**: `docker-compose.coolify.yml`.
5. **Domain**: configure your hostname; Coolify provisions the cert.
6. **Environment variables**: paste the values from your `.env` (Coolify treats
   them as secrets).
7. **Deploy**.

Coolify reads the `coolify.managed=true` labels and wires the router
automatically. No `proxy` network needed on your side.

---

## 3 · Validation Checklist

After deployment, walk through:

- [ ] `docker compose -f docker-compose.<flavour>.yml ps` shows `shlink-mcp` healthy (plus `redis` when `AUTH_REDIS_URL` is set)
- [ ] `curl -fsS https://<host>/healthz` returns `{"status":"ok"}`
- [ ] `curl -fsS https://<host>/.well-known/oauth-protected-resource` returns RFC 9728 JSON pointing at your IdP
- [ ] A real MCP client (Claude Web, mcp-inspector) completes the OAuth flow
- [ ] A test query — *"list my 5 newest short URLs"* — returns Shlink data

---

## 4 · Updating

The GHCR image is automatically rebuilt on every merge to `main`. To pull the
latest:

```bash
docker compose -f docker-compose.traefik.yml pull
docker compose -f docker-compose.traefik.yml up -d
```

OAuth state (DCR clients, refresh tokens, JTI mappings, in-flight transactions)
is persisted across container restarts so upgrading does *not* log existing
users out. Two backends — pick one per deployment, both encrypted at rest:

| Backend | Activated by | Encryption key | Persistence |
| --- | --- | --- | --- |
| Redis (recommended for production) | `AUTH_REDIS_URL` set | Operator-set `AUTH_STORAGE_ENCRYPTION_KEY` (Fernet) | Redis AOF volume |
| Disk fallback | `AUTH_REDIS_URL` empty | Derived from `AUTH_JWT_SIGNING_KEY` via HKDF | Bind volume at `AUTH_DISK_STORAGE_PATH` (default `/app/data/oauth-storage`, mounted by the bundled compose files) |

Either way, **keep `AUTH_JWT_SIGNING_KEY` stable across deploys** — it signs
the bearer tokens FastMCP issues to clients AND (in disk mode) derives the
storage encryption key. Same for `AUTH_STORAGE_ENCRYPTION_KEY` in Redis mode.

---

## 5 · Redis Sizing

> Skip this section if you're running the disk-storage fallback
> (`AUTH_REDIS_URL` empty). The bundled compose files start a Redis service
> by default but you can disable it for single-node deployments.

Redis holds OAuth state: Dynamic Client Registrations (DCR) plus issued
bearer/refresh tokens. The working set is tiny — single-digit megabytes for a
typical BAUER GROUP single-tenant deployment.

### What's actually stored

| Item | Approx. size | Lifetime |
| --- | --- | --- |
| One DCR client registration (Fernet-encrypted) | ~2-4 KB | Until manually revoked |
| One issued access token | ~1-2 KB | Default 1 h (refreshable) |
| One refresh token | ~1-2 KB | Default 24 h |
| One JWT signing-key entry | < 1 KB | Survives restarts via `AUTH_JWT_SIGNING_KEY` |

A connector (Claude Web, Claude Desktop, Copilot Studio, ...) registers once
per device/browser. Across the lifetime of the deployment you typically see
dozens of DCR clients, not thousands — one per (user × client app).

### The two configured limits are caps, not reservations

The compose files declare two limits that exist to *bound runaway growth*,
not to size the expected workload:

| Variable | Default | Purpose |
| --- | --- | --- |
| `REDIS_MAXMEMORY` | `256mb` | Redis's internal cap. Combined with `--maxmemory-policy noeviction`, new writes are **refused** when reached. We never silently drop OAuth state — operators must see and act on the anomaly. |
| `REDIS_CONTAINER_MEM_LIMIT` | `384m` | Docker cgroup limit. Defense in depth — if Redis ever exceeds its own cap (allocator fragmentation, AOF-rewrite buffer, connection storms), the kernel OOM-kills the container instead of the host. `restart: unless-stopped` replays AOF on the next start. |

The 128 MiB headroom between the two is intentional: Redis's RSS legitimately
exceeds `maxmemory` by 10-30% under normal operation (allocator fragmentation,
internal buffers).

### When to adjust

- **Reach the `maxmemory` cap?** Extremely unlikely. If you do, it usually
  means a bug (token cleanup not running) or a deliberate flood. Investigate
  before blindly raising the limit. Symptom: clients see `OOM command not
  allowed when used memory > 'maxmemory'` in MCP-server logs.
- **OOM-kills of the Redis container?** Indicates Redis exceeded `maxmemory`
  without honouring it (very rare). Bump both values proportionally —
  e.g. `REDIS_MAXMEMORY=512mb` + `REDIS_CONTAINER_MEM_LIMIT=768m`.
- **Multi-tenant scale-out with hundreds of independent connector apps?**
  Stays comfortably under 256 MiB. The caps don't need touching.

### Inspect at runtime

```bash
docker exec bg-shlink-mcp-redis redis-cli info memory \
  | grep -E "used_memory_human|maxmemory_human|used_memory_peak_human"
```

Typical output on a quiet single-tenant deployment:

```text
used_memory_human:1.42M
used_memory_peak_human:3.18M
maxmemory_human:256.00M
```

If `used_memory_human` ever approaches `maxmemory_human`, dig into
`docker exec bg-shlink-mcp-redis redis-cli --bigkeys` before raising the cap.

---

## 6 · Uninstall

```bash
docker compose -f docker-compose.<flavour>.yml down -v   # -v wipes Redis state
```

Removing `-v` keeps the Redis volume so you can roll back.
