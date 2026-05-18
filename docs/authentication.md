# Authentication

The MCP server has **two independent trust boundaries**:

| Layer | Direction | Mechanism | Configured via |
| --- | --- | --- | --- |
| MCP ↔ AI client | inbound | OAuth 2.1 + PKCE via upstream IdP | `AUTH_MODE` + provider env block |
| MCP → Shlink | outbound | Static API key, `X-Api-Key` header | `SHLINK_URL`, `SHLINK_API_KEY` |

The Shlink API key never leaves the server. The OIDC token never reaches
Shlink. Always.

---

## Choosing `AUTH_MODE`

```text
AUTH_MODE=entra-single | entra-multi | google | oidc | none
```

| Value | Use when |
| --- | --- |
| `entra-single` | Single Entra tenant (most BAUER GROUP customer deployments) |
| `entra-multi` | Multiple Entra tenants need access (with explicit allowlist) |
| `google` | Google Workspace identity, domain-restricted |
| `oidc` | Any other OIDC provider — Authentik, Keycloak, Zitadel, Auth0, Okta, … |
| `none` | **Development only** — `ENVIRONMENT=development` is enforced by Pydantic |

---

## Microsoft Entra ID — Single Tenant

Two setup paths — pick one:

- **[Automated](#automated-setup-recommended)** — one script call, ~10 seconds, reproducible. Recommended for any environment beyond a quick demo.
- **[Manual](#manual-setup)** — Azure Portal click-through. Use when you don't have `az` CLI handy or want to learn what the script does step-by-step.

Both end at the same place — three values for your `.env`. The script also handles **secret rotation** (Azure caps secrets at 24 months, so this comes up every two years).

### Why a custom API scope is needed

FastMCP's `AzureProvider` requires at least one **custom API scope** (`access_as_user` by default). OIDC scopes like `openid`/`profile`/`email`/`offline_access` are never present in Azure access token `scp` claims and can't be enforced server-side — Azure access tokens are *resource-scoped*, and your MCP needs its own resource identifier to validate them. Both setup paths below create this scope under "Expose an API".

---

### Automated setup (recommended)

Cross-platform script — PowerShell on Windows, Bash on Linux/macOS. Requires the Azure CLI (`winget install Microsoft.AzureCLI` or `brew install azure-cli`); the Bash version additionally needs `jq`.

The script provisions: app registration → redirect URIs (local + production) → Graph OIDC permissions → custom API scope under "Expose an API" → v2 access tokens → client secret → tenant-wide admin consent.

```powershell
# Windows / PowerShell 7+
.\scripts\setup-azure-app.ps1 `
    -TenantId <tenant-guid> `
    -Hostname mcp.example.com
```

```bash
# Linux / macOS
./scripts/setup-azure-app.sh \
    --tenant-id <tenant-guid> \
    --hostname mcp.example.com
```

The signed-in user needs **Application Administrator** role (or app ownership + `Application.ReadWrite.OwnedBy`). For tenant-wide admin consent additionally one of: Global Admin, Privileged Role Administrator, or Cloud Application Administrator. If you lack the consent role, run with `--no-admin-consent` and grant manually via Portal.

At the end the script prints a `.env`-ready block — paste it into your `.env`:

```ini
ENTRA_CLIENT_ID=<generated app id>
ENTRA_CLIENT_SECRET=<generated secret, shown once>
ENTRA_TENANT_ID=<tenant guid>
```

**Secret rotation** — Azure secrets expire at 24 months. Generate a fresh one without touching the rest of the app registration:

```powershell
.\scripts\setup-azure-app.ps1 -Mode rotate-secret `
    -TenantId <tenant-guid> -ClientId <existing-client-id>
```

```bash
./scripts/setup-azure-app.sh --mode rotate-secret \
    --tenant-id <tenant-guid> --client-id <existing-client-id>
```

The old secret stays valid in parallel (rolling rotation) — delete it once production has flipped to the new one:

```bash
az ad app credential delete --id <client-id> --key-id <old-key-id>
```

Other useful flags (both scripts):

| Flag | Default | What it changes |
| --- | --- | --- |
| `--scope-name` / `-ScopeName` | `access_as_user` | Use a different custom scope name (e.g. for granular `shlink.read`/`shlink.write`) |
| `--local-port` / `-LocalPort` | `8000` | Override the dev redirect URI port |
| `--secret-years` / `-SecretYears` | `2` | Secret lifetime (Azure max: 2) |
| `--include-graph-user-read` / `-IncludeGraphUserRead` | off | Also request `User.Read` (only if MCP tools call Graph) |
| `--no-admin-consent` / `-NoAdminConsent` | off | Skip consent step; grant manually via Portal |

---

### Manual setup

If you'd rather click through the Azure Portal (or your auditor needs to see the screenshots):

#### 1 · App registration

1. <https://portal.azure.com> → Microsoft Entra ID → App registrations → **New registration**
2. **Name**: `Shlink MCP Server` (or your own naming convention)
3. **Supported account types**: *Accounts in this organizational directory only (Single tenant)*
4. **Redirect URI**: Platform = **Web**, URI = `${PUBLIC_BASE_URL}/auth/callback`
5. Save

#### 2 · API permissions (delegated, Microsoft Graph)

Add: `openid`, `profile`, `email`, `offline_access`. `User.Read` is optional — only needed if MCP tools call Graph on behalf of the user.

Click **Grant admin consent for &lt;tenant&gt;**.

#### 3 · Expose an API

This step is the one most setup guides skip. FastMCP needs a custom resource scope on this app — without it, the server refuses to start.

1. **Expose an API** → **Set** next to "Application ID URI" → accept default `api://<client-id>`
2. **Add a scope**:

   | Field | Value |
   | --- | --- |
   | Scope name | `access_as_user` |
   | Who can consent | **Admins and users** |
   | Admin consent display name | `Access Shlink MCP as User` |
   | Admin consent description | `Allows the app to call the Shlink MCP server on behalf of the signed-in user.` |
   | State | **Enabled** |

3. Back to **API permissions** → **Add a permission** → **My APIs** → select this same app → tick `access_as_user` → **Add permissions** → **Grant admin consent**

#### 4 · v2 access tokens (manifest)

Manifest → set `"accessTokenAcceptedVersion": 2` (legacy schema) or `"requestedAccessTokenVersion": 2` (Microsoft Graph schema — Azure migrates tenants between the two; whichever appears in your manifest is the one to edit). Save.

Without v2, Azure issues legacy tokens that have no `scp` claim and FastMCP will reject every request as `insufficient_scope`.

#### 5 · Client secret

Certificates & secrets → **New client secret** → copy the **Value** (not the secret ID, not the hint — the full Value, shown once).

#### 6 · .env

```ini
AUTH_MODE=entra-single
ENTRA_CLIENT_ID=<Application (client) ID>
ENTRA_CLIENT_SECRET=<the Value you copied>
ENTRA_TENANT_ID=<Directory (tenant) ID>
# ENTRA_API_SCOPES defaults to "access_as_user" - matches the scope from step 3
# ENTRA_EXTRA_SCOPES is for Graph OBO calls (e.g. User.Read) - leave empty
```

### Redirect URI table

| Setting | Value |
| --- | --- |
| MCP `PUBLIC_BASE_URL` | `https://shlink-mcp.example.com` |
| Entra Redirect URI | `https://shlink-mcp.example.com/auth/callback` |
| **MUST match exactly** | scheme, host, case, no trailing slash |

---

## Microsoft Entra ID — Multi Tenant

Same setup as single-tenant **except**:

- App Registration → *Accounts in any organizational directory*.
- `ENTRA_TENANT_ID` is set to:
  - `organizations` — work/school accounts from any tenant
  - `common` — work/school + personal Microsoft accounts
- `ENTRA_ALLOWED_TENANTS` **must** be set to the comma-separated list of GUIDs
  you actually want to allow — without it, *anyone* with a Microsoft account
  in any tenant on the planet can call your MCP.

```ini
AUTH_MODE=entra-multi
ENTRA_CLIENT_ID=<…>
ENTRA_CLIENT_SECRET=<…>
ENTRA_TENANT_ID=organizations
ENTRA_ALLOWED_TENANTS=11111111-1111-1111-1111-111111111111,2222...
```

The provider validates JWT signatures via the multi-tenant JWKS endpoint, then
a post-issuance check rejects tokens whose `tid` claim isn't on the allowlist.

---

## Google Workspace

### 1 · OAuth client

1. <https://console.cloud.google.com/apis/credentials>
2. **Create credentials → OAuth client ID**.
3. **Application type**: Web application.
4. **Authorized redirect URI**: `${PUBLIC_BASE_URL}/auth/callback`.

### 2 · OAuth consent screen

- **User type**: Internal (Workspace-restricted) or External.
- **Scopes**: `openid`, `email`, `profile`.

### 3 · .env

```ini
AUTH_MODE=google
GOOGLE_CLIENT_ID=<client ID>
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_ALLOWED_DOMAINS=bauer-group.com
```

`GOOGLE_ALLOWED_DOMAINS` checks the token's `hd` claim. Workspace accounts
ship one; personal Gmail accounts do not — leaving the var empty allows
personal Gmail.

| Setting | Value |
| --- | --- |
| MCP `PUBLIC_BASE_URL` | `https://shlink-mcp.example.com` |
| Google Redirect URI | `https://shlink-mcp.example.com/auth/callback` |

---

## Generic OIDC

Works with anything that speaks standard OIDC: Authentik, Keycloak, Zitadel,
Auth0, Okta, Cognito, …

### 1 · Discovery URL path (recommended)

If your IdP exposes a discovery doc at
`https://auth.example.com/.well-known/openid-configuration`, just point at it
— the loader picks up endpoints + JWKS rotations automatically.

```ini
AUTH_MODE=oidc
OIDC_DISCOVERY_URL=https://auth.bauer-group.com/application/o/shlink-mcp/.well-known/openid-configuration
OIDC_CLIENT_ID=<…>
OIDC_CLIENT_SECRET=<…>
OIDC_SCOPES=openid profile email
```

### 2 · Explicit endpoints path

For IdPs without discovery, or when you need to lock URIs for compliance:

```ini
AUTH_MODE=oidc
OIDC_ISSUER=https://auth.example.com/
OIDC_AUTH_URI=https://auth.example.com/oauth/authorize
OIDC_TOKEN_URI=https://auth.example.com/oauth/token
OIDC_JWKS_URI=https://auth.example.com/oauth/jwks
OIDC_USERINFO_URI=https://auth.example.com/oauth/userinfo
OIDC_CLIENT_ID=<…>
OIDC_CLIENT_SECRET=<…>
OIDC_SCOPES=openid profile email
```

### Redirect URI

```text
${PUBLIC_BASE_URL}/auth/callback
```

> **Note:** FastMCP's `OAuthProxy` uses a single redirect path (`/auth/callback`)
> for **every** upstream provider (Entra, Google, generic OIDC). There is no
> per-provider sub-path — the IdP only needs to round-trip the user back, not
> identify itself in the URL.

### Per-IdP redirect URI table

| IdP | Configuration UI path | Required value |
| --- | --- | --- |
| Authentik | Applications → Providers → OAuth2 Provider → Redirect URIs | `https://your-host/auth/callback` |
| Keycloak | Realm → Clients → your-client → Valid Redirect URIs | `https://your-host/auth/callback` |
| Zitadel | Projects → your-project → Applications → Redirect URIs | `https://your-host/auth/callback` |
| Auth0 | Applications → your-app → Allowed Callback URLs | `https://your-host/auth/callback` |
| Okta | Applications → your-app → General → Sign-in redirect URIs | `https://your-host/auth/callback` |

---

## OAuth Persistence

Two configuration concerns survive across container restarts:

### `AUTH_JWT_SIGNING_KEY`

Signs the bearer tokens FastMCP issues to clients. **Must** be stable across
restarts — otherwise every redeploy invalidates active sessions and forces
every user to re-auth.

Generate once and store securely:

```bash
openssl rand -hex 32
```

### `AUTH_REDIS_URL` + `AUTH_STORAGE_ENCRYPTION_KEY`

Redis stores OAuth Dynamic Client Registrations (DCR). The Fernet key
encrypts client secrets at rest so plaintext never lands in Redis.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The Traefik and Coolify compose files ship a Redis service by default.

---

## Verifying Auth

After deployment, the MCP server publishes its OAuth metadata at:

```text
${PUBLIC_BASE_URL}/.well-known/oauth-protected-resource
```

A correct response includes:

```json
{
  "resource": "https://your-host/mcp",
  "authorization_servers": ["https://your-idp/..."]
}
```

An unauthenticated request to `/mcp` should return `401` with a
`WWW-Authenticate` header carrying `resource_metadata=`.

---

## FAQ

**Q: Why isn't the Shlink API key per-user?**
Shlink has no concept of per-user API keys. The configured key is a single
trust anchor; OIDC decides *who* may invoke a tool, not *what* the tool can do
in Shlink.

**Q: Can I rotate `SHLINK_API_KEY` without downtime?**
Generate a new key in Shlink, set it in `.env`, and run
`docker compose up -d` — only the new key is sent on subsequent requests.
There's a brief moment where in-flight requests with the old key will fail;
that's acceptable for a low-traffic admin service.

**Q: How do I know which tenant the caller comes from?**
The JWT `tid` (Entra) / `hd` (Google) / `iss` (generic OIDC) claims surface
via FastMCP context. Logs include `caller.tid` / `caller.iss` for audit.
