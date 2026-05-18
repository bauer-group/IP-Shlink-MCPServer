#!/usr/bin/env python3
"""
dev-inspector.py — MCP debugging via Inspector (local + remote).

Three modes under one script:

  default (local stdio):  build image, docker run -i + Inspector, AUTH_MODE=none
  --http <PORT>          : build image, detached on host port, Inspector GUI
  --remote <URL>         : preflight-probe deployed instance, then Inspector GUI

Local modes always build the image from your working tree (same build
command shape as docker-compose.development.yml: target=production, with
the SHLINK_OPENAPI_VERSION passthrough) and then run it.

The local modes need a populated .env (SHLINK_URL + SHLINK_API_KEY).
The remote mode needs only the URL — the deployed server owns its own config.

Examples:
  python scripts/dev-inspector.py
  python scripts/dev-inspector.py --http --port 8000
  python scripts/dev-inspector.py --network bg-shlink-mcp
  python scripts/dev-inspector.py --remote https://shlink-mcp.example.com/mcp
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


LOCAL_IMAGE = "bg-shlink-mcp:dev"
APP_CONTEXT = "app/bg-shlink-mcp"
PROBE_TIMEOUT = 10


# ── helpers ──────────────────────────────────────────────────────────────────


def compose_build_args(env: dict[str, str]) -> list[str]:
    """Mirror compose's `args: SHLINK_OPENAPI_VERSION:` passthrough — forward
    the var only when set, so an unset value lets the Dockerfile ARG default
    win instead of clobbering it with an empty string."""
    flags: list[str] = []
    spec_version = env.get("SHLINK_OPENAPI_VERSION", "").strip()
    if spec_version:
        flags.extend(["--build-arg", f"SHLINK_OPENAPI_VERSION={spec_version}"])
    return flags


def load_dotenv(env_path: Path) -> dict[str, str]:
    """Minimal .env parser — no shell expansion, no multiline-quote tricks."""
    if not env_path.exists():
        return {}
    env: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def require_setting(env: dict[str, str], key: str) -> str:
    val = env.get(key, "").strip()
    if not val or val.startswith("CHANGE_ME"):
        sys.exit(
            f"error: {key} is missing or still set to the CHANGE_ME placeholder.\n"
            "       Fill .env first (see scripts/generate-env.py + docs/installation.md)."
        )
    return val


def require_command(name: str) -> None:
    if not shutil.which(name):
        sys.exit(f"error: '{name}' is not on PATH — install it first")


def npx_cmd() -> str:
    """Resolved npx path — Windows ships npx as npx.cmd, and subprocess
    on Windows bypasses PATHEXT, so the bare name fails with WinError 2."""
    return shutil.which("npx") or "npx"


def env_flags(env: dict[str, str]) -> list[str]:
    """Forward only the keys the server actually reads — never the whole .env."""
    pass_through = [
        "SHLINK_URL",
        "SHLINK_API_KEY",
        "SHLINK_OPENAPI_URL",
        "SHLINK_OPENAPI_REFRESH_INTERVAL",
        "SHLINK_HTTP_TIMEOUT",
        "LOG_LEVEL",
    ]
    flags: list[str] = []
    for key in pass_through:
        if env.get(key):
            flags.extend(["-e", f"{key}={env[key]}"])
    flags.extend(
        [
            "-e", "AUTH_MODE=none",
            "-e", "ENVIRONMENT=development",
            "-e", "LOG_FORMAT=console",
            "-e", "AUTH_JWT_SIGNING_KEY=dev-only-do-not-use-in-production-"
                  "deadbeef00000000000000000000000000",
        ]
    )
    return flags


# ── local: stdio ─────────────────────────────────────────────────────────────


def run_stdio(image: str, env: dict[str, str], network: str | None) -> int:
    cmd = [
        npx_cmd(), "-y", "@modelcontextprotocol/inspector",
        "docker", "run", "--rm", "-i",
        *(["--network", network] if network else []),
        *env_flags(env),
        image,
        "python", "src/main.py", "serve", "--transport", "stdio",
    ]
    print(">>> launching Inspector (local stdio mode)")
    print("    Inspector spawns the container; closing Inspector stops it.")
    print("    Ctrl+C to exit.\n")
    return subprocess.call(cmd)


# ── local: HTTP detached ─────────────────────────────────────────────────────


def run_http(
    image: str, env: dict[str, str], port: int, network: str | None
) -> int:
    container = "bg-shlink-mcp-dev-inspector"
    subprocess.run(
        ["docker", "rm", "-f", container],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    docker_cmd = [
        "docker", "run", "--rm", "-d",
        "--name", container,
        "-p", f"{port}:8000",
        *(["--network", network] if network else []),
        *env_flags(env),
        "-e", "MCP_TRANSPORT=streamable-http",
        "-e", "MCP_HOST=0.0.0.0",
        "-e", "MCP_PORT=8000",
        "-e", f"PUBLIC_BASE_URL=http://localhost:{port}",
        image,
    ]
    subprocess.run(docker_cmd, check=True)

    url = f"http://localhost:{port}/mcp"
    print(f">>> local HTTP container '{container}' running")
    print(f"    URL to paste into Inspector: {url}")
    print(f"    Tail logs: docker logs -f {container}")
    print(f"    Stop:      docker stop {container}\n")

    return subprocess.call([npx_cmd(), "-y", "@modelcontextprotocol/inspector"])


# ── remote: preflight + Inspector ────────────────────────────────────────────


def _http_get_json(url: str) -> tuple[int, dict | None, str | None]:
    """GET → (status, parsed_json_or_None, error_message_or_None)."""
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body), None
            except json.JSONDecodeError:
                return resp.status, None, "non-JSON body"
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.reason
    except urllib.error.URLError as exc:
        return 0, None, str(exc.reason)


def preflight_remote(mcp_url: str) -> None:
    """Probe a deployed instance — surface deploy issues before Inspector starts."""
    base = mcp_url.rstrip("/")
    if base.endswith("/mcp"):
        base = base[:-4]

    print(f">>> preflight: {base}")

    # 1. liveness
    status, _, err = _http_get_json(f"{base}/healthz")
    if status == 200:
        print(f"    /healthz                                   ok ({status})")
    else:
        sys.exit(
            f"    /healthz                                   FAILED ({err or status})\n"
            "       Server unreachable or unhealthy — fix the deploy first."
        )

    # 2. RFC 9728 protected-resource metadata
    status, body, err = _http_get_json(f"{base}/.well-known/oauth-protected-resource")
    if status == 200 and body:
        print(f"    /.well-known/oauth-protected-resource      ok ({status})")
        if "authorization_servers" in body:
            for srv in body["authorization_servers"]:
                print(f"        authorization_server: {srv}")
        if "resource" in body:
            print(f"        resource:             {body['resource']}")
    else:
        print(
            f"    /.well-known/oauth-protected-resource      FAILED "
            f"({err or status}) — Inspector won't be able to discover the IdP"
        )

    # 3. OAuth server metadata (forwarded by FastMCP)
    status, body, err = _http_get_json(f"{base}/.well-known/oauth-authorization-server")
    if status == 200 and body:
        print(f"    /.well-known/oauth-authorization-server    ok ({status})")
        for field in ("issuer", "authorization_endpoint", "token_endpoint",
                      "registration_endpoint"):
            if field in body:
                print(f"        {field:22} {body[field]}")
    else:
        print(
            f"    /.well-known/oauth-authorization-server    FAILED "
            f"({err or status}) — IdP discovery is broken"
        )

    # 4. /mcp must gate (expect 401 + WWW-Authenticate)
    req = urllib.request.Request(
        f"{base}/mcp",
        method="POST",
        data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            print(
                f"    POST /mcp                                  "
                f"UNEXPECTED {resp.status} (expected 401 — is AUTH_MODE=none?)"
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            www = exc.headers.get("WWW-Authenticate", "")
            print(f"    POST /mcp (unauth)                         ok (401 gated)")
            if www:
                preview = (www[:78] + "…") if len(www) > 80 else www
                print(f"        WWW-Authenticate: {preview}")
        else:
            print(
                f"    POST /mcp (unauth)                         "
                f"unexpected {exc.code} (expected 401)"
            )
    except urllib.error.URLError as exc:
        print(f"    POST /mcp (unauth)                         FAILED ({exc.reason})")


def run_remote(mcp_url: str) -> int:
    preflight_remote(mcp_url)
    print()
    print(">>> launching Inspector (remote OIDC mode)")
    print(f"    Paste this URL into the GUI:  {mcp_url}")
    print("    Inspector will discover the IdP and open a browser tab for OAuth.\n")
    return subprocess.call([npx_cmd(), "-y", "@modelcontextprotocol/inspector"])


# ── entrypoint ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MCP debugging via Inspector — local (no auth) or remote (OIDC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--http",
        action="store_true",
        help="Local HTTP mode (detached container) instead of stdio",
    )
    mode.add_argument(
        "--remote",
        metavar="URL",
        default=None,
        help="Remote MCP endpoint (e.g. https://shlink-mcp.example.com/mcp). "
        "Runs preflight probes and launches Inspector; no local container.",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Host port for --http mode (default: 8000)",
    )

    parser.add_argument(
        "--image",
        default=LOCAL_IMAGE,
        help=f"Override the local image tag (default: {LOCAL_IMAGE}). "
        "The script always builds this tag from app/bg-shlink-mcp — the "
        "tag is local-only and never pushed.",
    )
    parser.add_argument(
        "--network",
        default=None,
        help="Docker network to attach (use when SHLINK_URL resolves via a "
        "compose-internal alias, e.g. --network bg-shlink-mcp)",
    )
    args = parser.parse_args()

    require_command("npx")

    # ── remote mode: no docker, no .env ──
    if args.remote:
        if args.image != LOCAL_IMAGE or args.network:
            print(
                "warning: --image/--network are ignored in --remote mode",
                file=sys.stderr,
            )
        return run_remote(args.remote)

    # ── local modes: need docker + .env ──
    require_command("docker")
    project_root = Path(__file__).resolve().parent.parent
    env = load_dotenv(project_root / ".env")
    require_setting(env, "SHLINK_URL")
    require_setting(env, "SHLINK_API_KEY")

    os.chdir(project_root)
    build_cmd = [
        "docker", "build", "-t", args.image,
        "--target", "production",
        *compose_build_args(env),
        APP_CONTEXT,
    ]
    print(f">>> {' '.join(build_cmd)}")
    subprocess.run(build_cmd, check=True)

    if args.http:
        return run_http(args.image, env, args.port, args.network)
    return run_stdio(args.image, env, args.network)


if __name__ == "__main__":
    sys.exit(main())
