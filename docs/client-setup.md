# Client Setup

How to connect each major AI assistant to your Shlink MCP server.

Common preconditions:

- The server is reachable at `https://<your-host>/mcp`
- `/.well-known/oauth-protected-resource` returns 200 OK with valid JSON
- Your IdP App Registration includes the right redirect URI (see
  [authentication.md](authentication.md))

---

## Claude Web Connectors

1. Open https://claude.ai in a browser.
2. **Settings → Connectors → Add custom connector**.
3. Fill in:
   - **Name**: `Shlink`
   - **URL**: `https://<your-host>/mcp`
4. Click **Connect**. A browser tab opens for the OAuth flow.
5. Sign in via your IdP — Claude completes the dance and lists the tools.
6. Test in any conversation:
   > "Use the Shlink connector to list my 5 newest short URLs."

The connector is persistent across browser sessions. To remove it: Settings →
Connectors → ⋯ → Disconnect.

---

## Claude Desktop

Claude Desktop reads `~/.config/claude/claude_desktop_config.json` (macOS /
Linux) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).

Add an entry under `mcpServers`:

```json
{
  "mcpServers": {
    "shlink": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://<your-host>/mcp"
      ]
    }
  }
}
```

`mcp-remote` is a small Node.js bridge that handles the OAuth dance on
Claude Desktop's behalf. Install once with `npm i -g mcp-remote` (or rely on
`npx` to fetch it on demand).

Restart Claude Desktop. The Shlink tools appear in the tool picker.

---

## Microsoft 365 Copilot Studio

1. Open https://copilotstudio.microsoft.com.
2. **Tools → Add a tool → New tool → Custom connector → MCP**.
3. **Server URL**: `https://<your-host>/mcp`.
4. **Authentication**: OAuth 2.0.
   - **Authorization URL**: `https://<your-host>/authorize`
   - **Token URL**: `https://<your-host>/token`
   - **Scopes**: leave empty (FastMCP advertises the right scopes via
     `/.well-known/oauth-authorization-server`).
5. **Test connection** — Copilot Studio opens an OAuth window.
6. **Publish** the connector.
7. In a Copilot, add the Shlink connector and prompt:
   > "List my Shlink short URLs created this week."

---

## ChatGPT Connectors

1. https://chatgpt.com → **Settings → Connectors → Add new**.
2. **Connector type**: Custom MCP.
3. **URL**: `https://<your-host>/mcp`.
4. Approve the OAuth flow.
5. In any conversation:
   > "@Shlink list my 5 newest short URLs"

---

## Cursor

Cursor reads `~/.cursor/mcp.json` (per-user) or `.cursor/mcp.json` in the
workspace (per-project).

```json
{
  "mcpServers": {
    "shlink": {
      "url": "https://<your-host>/mcp",
      "transport": "streamable-http"
    }
  }
}
```

Reload the Cursor window. The Shlink tools surface in the chat sidebar.

---

## Continue (VS Code / JetBrains)

Add to your `~/.continue/config.json`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "streamable-http",
          "url": "https://<your-host>/mcp"
        }
      }
    ]
  }
}
```

Continue handles the OAuth flow on first invocation.

---

## Self-Test with MCP Inspector

For development without any of the GUI clients above — or before pointing
real clients at a fresh deployment — use the official MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

It opens a local web UI on `http://localhost:6274` where you can drive the
OAuth dance, browse the auto-generated tool list, and send `tools/list`,
`tools/call`, and `resources/read` requests directly.

Full walkthrough — local stdio, local HTTP, remote OAuth-gated, headless
CI mode — in [testing.md](testing.md).

---

## Sanity Checks

If a client cannot connect, walk through:

```bash
# 1. Public reachability
curl -fsS https://<your-host>/healthz                                  # → {"status":"ok"}

# 2. Protected-resource metadata
curl -fsS https://<your-host>/.well-known/oauth-protected-resource | jq .

# 3. OAuth-server metadata (forwarded by FastMCP)
curl -fsS https://<your-host>/.well-known/oauth-authorization-server | jq .

# 4. Confirm /mcp gates correctly
curl -i -X POST https://<your-host>/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# Expect: HTTP/1.1 401 + WWW-Authenticate header
```

For deeper failures see [troubleshooting.md](troubleshooting.md).
