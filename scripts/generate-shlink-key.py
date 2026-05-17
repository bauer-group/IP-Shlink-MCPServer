#!/usr/bin/env python3
"""
generate-shlink-key.py — Mint a Shlink API key and write it into `.env`.

Cross-platform (Windows / Linux / macOS), pure stdlib, no dependencies.

Why this script exists
----------------------
Shlink's REST API does NOT expose API-key management — keys can only be
created via the `shlink api-key:generate` CLI inside the Shlink container.
This script wraps that one-liner and writes the resulting key (plus the
Shlink base URL) into `.env`, so the MCP server can be wired up in a single
command instead of copy/pasting between two terminals.

Usage
-----
From the repo root:

    python scripts/generate-shlink-key.py \\
        --container shlink \\
        --shlink-url http://shlink:8080 \\
        --name mcp-server

    python scripts/generate-shlink-key.py --dry-run    # show command, don't run it
    python scripts/generate-shlink-key.py --print      # print key to stdout, skip .env write

Exit codes
----------
    0  success
    1  precondition failed (docker missing, container missing, .env missing)
    2  Shlink CLI failed or returned an unparseable response
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


# --- Configuration -----------------------------------------------------------

DEFAULT_CONTAINER = "shlink"
DEFAULT_KEY_NAME = "mcp-server"
DEFAULT_ENV_FILE = ".env"

# Shlink prints the new key on a line that looks like:
#   [OK] Generated API key: "abc123..."
# or (older releases / different formatters):
#   Generated API key "abc123..."
# We match the longest hex/dash run on any line containing "API key".
KEY_LINE_RE = re.compile(
    r"API key[^A-Za-z0-9]*[\"']?([A-Za-z0-9][A-Za-z0-9_\-]{16,})[\"']?",
    re.IGNORECASE,
)


# --- Shlink CLI invocation ---------------------------------------------------

def build_role_args(args: argparse.Namespace) -> list[str]:
    """
    Translate CLI flags into Shlink `api-key:generate` role-restriction args.

    Shlink supports exactly three role flags (verified against v5.0.1
    `module/Rest/src/ApiKey/Role.php`):

        --author-only                  see only short URLs created by this key
        --domain-only=<authority>      restrict to a single Shlink domain
        --no-orphan-visits             hide orphan-visit data

    Why "read-only" is not in this list
    -----------------------------------
    Shlink's permission model expresses *scope* (which URLs the key can
    touch), not *operation* (whether it can read vs. write). Every key
    that can read a URL can also write to it within its scope. There is
    no upstream concept of a read-only key.

    Read/write separation in this stack is enforced one layer up: the
    MCP server tags every mutating tool with `destructiveHint=True` so
    MCP clients gate the call behind human approval. The Shlink key
    itself stays admin-equivalent — see app/bg-shlink-mcp/src/shlink/
    tool_mapper.py for the policy table.

    Optional scope narrowing still helps reduce blast radius if `.env`
    leaks: an `--author-only` key, for instance, can only damage URLs
    it created itself.
    """
    role_args: list[str] = []
    if args.author_only:
        role_args.append("--author-only")
    if args.role_domain:
        role_args.append(f"--domain-only={args.role_domain}")
    if args.no_orphan:
        role_args.append("--no-orphan-visits")
    return role_args


def run_shlink_cli(
    container: str,
    key_name: str,
    role_args: list[str],
    dry_run: bool,
) -> str:
    """
    Run `docker exec <container> shlink api-key:generate ...` and return the
    raw stdout. In --dry-run mode, print the command and return an empty
    string without executing.
    """
    cmd = [
        "docker", "exec", container,
        "shlink", "api-key:generate",
        f"--name={key_name}",
        *role_args,
    ]

    if dry_run:
        print("would run:", " ".join(_shell_quote(c) for c in cmd))
        return ""

    if shutil.which("docker") is None:
        raise FileNotFoundError(
            "docker CLI not found on PATH — install Docker Desktop / docker engine first."
        )

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise RuntimeError(
            f"docker exec exited {proc.returncode} — is the '{container}' container running?"
        )
    return proc.stdout


def _shell_quote(value: str) -> str:
    """Minimal cross-platform quoting for the dry-run command preview."""
    if not value or any(ch in value for ch in (" ", "\t", '"', "'", "$", "`")):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def parse_key(cli_output: str) -> str:
    """
    Extract the freshly-minted API key from the CLI output.

    Shlink wraps the key in quotes and prefixes it with "Generated API key:";
    we tolerate small wording variants by matching on "API key" + the
    longest plausible token on the same line.
    """
    for line in cli_output.splitlines():
        m = KEY_LINE_RE.search(line)
        if m:
            return m.group(1)
    raise ValueError(
        "could not locate the API key in Shlink CLI output. "
        "Full output follows:\n" + cli_output
    )


# --- .env writer -------------------------------------------------------------

def upsert_env_value(env_text: str, key: str, value: str) -> tuple[str, str]:
    """
    Set `key=value` in the .env text. If the key exists (commented or not),
    its line is replaced in-place; otherwise the pair is appended.

    Returns the new text and a one-word action: 'replaced' | 'appended'.
    """
    pattern = re.compile(rf"^{re.escape(key)}=[^\r\n]*$", re.MULTILINE)
    if pattern.search(env_text):
        new_text = pattern.sub(f"{key}={value}", env_text, count=1)
        return new_text, "replaced"
    suffix = "" if env_text.endswith("\n") else "\n"
    return env_text + suffix + f"{key}={value}\n", "appended"


# --- CLI ---------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="generate-shlink-key.py",
        description=(
            "Mint a Shlink API key via `docker exec` and write it into .env. "
            "Optionally also writes SHLINK_URL in the same pass."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--container", default=DEFAULT_CONTAINER,
        help=f"name of the running Shlink container (default: {DEFAULT_CONTAINER})",
    )
    p.add_argument(
        "--name", default=DEFAULT_KEY_NAME,
        help=f"human-readable name for the new key (default: {DEFAULT_KEY_NAME})",
    )
    p.add_argument(
        "--shlink-url", default=None,
        help="value to write into SHLINK_URL (omit to leave the existing value untouched)",
    )
    p.add_argument(
        "--env-file", default=DEFAULT_ENV_FILE,
        help=f"path to the .env file (default: {DEFAULT_ENV_FILE}, relative to repo root)",
    )

    # Role-restriction flags (consumed by build_role_args). All optional;
    # omitting them yields an admin-equivalent key. Read/write separation
    # is enforced in the MCP tool layer, not here.
    p.add_argument(
        "--author-only", action="store_true",
        help="restrict the key to URLs created by itself (AUTHORED_SHORT_URLS role)",
    )
    p.add_argument(
        "--role-domain", default=None,
        help="restrict the key to one Shlink domain authority (e.g. urls.example.com)",
    )
    p.add_argument(
        "--no-orphan", action="store_true",
        help="hide orphan visits from the new key",
    )

    p.add_argument(
        "--dry-run", action="store_true",
        help="print the docker command and exit; don't run it and don't touch .env",
    )
    p.add_argument(
        "--print", action="store_true", dest="print_only",
        help="print the new key to stdout instead of writing to .env",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = repo_root / env_path

    # --- Preconditions ---
    if not args.dry_run and not args.print_only and not env_path.exists():
        print(
            f"error: {env_path} not found — generate it first with "
            f"`python scripts/generate-env.py`.",
            file=sys.stderr,
        )
        return 1

    # --- Build role args (admin-equivalent by default; CLI flags narrow scope) ---
    role_args = build_role_args(args)

    # --- Mint the key ---
    try:
        cli_output = run_shlink_cli(
            container=args.container,
            key_name=args.name,
            role_args=role_args,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        return 0

    try:
        new_key = parse_key(cli_output)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.print_only:
        sys.stdout.write(new_key + "\n")
        return 0

    # --- Write back to .env (with .bak) ---
    env_text = env_path.read_text(encoding="utf-8")
    backup = env_path.with_suffix(env_path.suffix + ".bak")
    shutil.copy2(env_path, backup)

    env_text, key_action = upsert_env_value(env_text, "SHLINK_API_KEY", new_key)
    url_action: str | None = None
    if args.shlink_url:
        env_text, url_action = upsert_env_value(env_text, "SHLINK_URL", args.shlink_url)

    env_path.write_text(env_text, encoding="utf-8", newline="\n")

    # --- Report ---
    print(f"backup: {backup}")
    print(f"wrote:  {env_path}")
    print(f"  SHLINK_API_KEY  {key_action:<8}  {new_key[:4]}...{new_key[-4:]} ({len(new_key)} chars)")
    if url_action:
        print(f"  SHLINK_URL      {url_action:<8}  {args.shlink_url}")
    if role_args:
        print(f"  role scope      {' '.join(role_args)}")
    else:
        print("  role scope      (unrestricted — admin-equivalent)")
    return 0


def reconfigure_stdout_utf8() -> None:
    """Force stdout/stderr to UTF-8 (Windows default is cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


if __name__ == "__main__":
    reconfigure_stdout_utf8()
    sys.exit(main())
