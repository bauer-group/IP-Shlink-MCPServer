"""Tests for the token-bucket rate limiter.

Two layers are exercised:
- `resolve_client_id` (pure function) — every interesting permutation of
  auth-state × XFF-state × direct-IP × trusted-hops, no FastMCP context.
- `build_rate_limit_middleware` (settings adapter) — disabled vs enabled,
  default vs custom burst, global vs per-client.

The middleware adapter that reads from FastMCP's context vars
(`_get_client_id_from_context`) is intentionally NOT exercised here — its
job is plumbing (call helpers, pass values to the pure function), and
testing it would require constructing a Starlette request scope + binding
FastMCP's context vars. The pure function tests cover every routing
decision the adapter can make.
"""

from __future__ import annotations

from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

from rate_limit import build_rate_limit_middleware, resolve_client_id


# ── resolve_client_id: pure routing ─────────────────────────────────────────


def test_authenticated_request_keys_on_subject():
    """Subject wins over IP — same user across IPs hits the same bucket."""
    cid = resolve_client_id(
        auth_subject="u-42",
        xff_header="9.9.9.9",
        direct_remote_ip="10.0.0.1",
        trusted_proxy_hops=1,
    )
    assert cid == "sub:u-42"


def test_xff_used_when_anonymous_and_proxy_configured():
    """Single-hop proxy: rightmost (and only) XFF entry is the trusted IP."""
    cid = resolve_client_id(
        auth_subject=None,
        xff_header="203.0.113.7",
        direct_remote_ip="10.0.0.1",
        trusted_proxy_hops=1,
    )
    assert cid == "ip:203.0.113.7"


def test_xff_picks_rightmost_minus_n_with_multiple_hops():
    """2 hops: position -2 is what the OUTERMOST proxy saw.

    XFF "client, edge_proxy, ingress_proxy" with trusted_proxy_hops=2:
    we trust 2 hops, so position -2 = "edge_proxy" — the address the
    outer proxy (edge) saw on the wire.
    """
    cid = resolve_client_id(
        auth_subject=None,
        xff_header="198.51.100.1, 203.0.113.7, 10.0.0.1",
        direct_remote_ip="10.0.0.1",
        trusted_proxy_hops=2,
    )
    assert cid == "ip:203.0.113.7"


def test_xff_with_fewer_hops_than_configured_clips_to_leftmost_observed():
    """Mis-configured (or unusual): XFF has 1 entry but we expect 2 hops.

    Don't trust beyond the list — clip to the leftmost observed entry,
    which is still proxy-written and safer than reading random memory.
    """
    cid = resolve_client_id(
        auth_subject=None,
        xff_header="203.0.113.7",
        direct_remote_ip="10.0.0.1",
        trusted_proxy_hops=2,
    )
    assert cid == "ip:203.0.113.7"


def test_trusted_hops_zero_ignores_xff_completely():
    """No proxy in front: XFF is attacker-controlled, must not be trusted."""
    cid = resolve_client_id(
        auth_subject=None,
        xff_header="8.8.8.8, 1.1.1.1",  # would be a spoof attempt
        direct_remote_ip="203.0.113.7",
        trusted_proxy_hops=0,
    )
    assert cid == "ip:203.0.113.7"


def test_falls_back_to_direct_ip_when_no_xff():
    """Direct connection, no proxy header present."""
    cid = resolve_client_id(
        auth_subject=None,
        xff_header=None,
        direct_remote_ip="203.0.113.7",
        trusted_proxy_hops=1,
    )
    assert cid == "ip:203.0.113.7"


def test_empty_xff_falls_through_to_direct_ip():
    """An empty/whitespace-only XFF should not blank-key the bucket."""
    cid = resolve_client_id(
        auth_subject=None,
        xff_header="   ,  ",
        direct_remote_ip="203.0.113.7",
        trusted_proxy_hops=1,
    )
    assert cid == "ip:203.0.113.7"


def test_anonymous_unknown_returns_sentinel():
    """No subject, no XFF, no direct IP — sentinel keeps the bucket finite."""
    cid = resolve_client_id(
        auth_subject=None,
        xff_header=None,
        direct_remote_ip=None,
        trusted_proxy_hops=1,
    )
    assert cid == "ip:unknown"


# ── build_rate_limit_middleware: settings adapter ───────────────────────────


def test_build_returns_none_when_disabled(monkeypatch, valid_base_env):
    monkeypatch.setenv("RATE_LIMITER_ENABLED", "false")
    from config import Settings

    assert build_rate_limit_middleware(Settings()) is None


def test_build_returns_middleware_with_explicit_burst(monkeypatch, valid_base_env):
    monkeypatch.setenv("RATE_LIMITER_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMITER_MAX_REQUESTS_PER_SECOND", "5")
    monkeypatch.setenv("RATE_LIMITER_BURST_CAPACITY", "13")
    monkeypatch.setenv("RATE_LIMITER_GLOBAL", "false")
    from config import Settings

    mw = build_rate_limit_middleware(Settings())
    assert isinstance(mw, RateLimitingMiddleware)
    assert mw.max_requests_per_second == 5.0
    assert mw.burst_capacity == 13
    assert mw.global_limit is False
    assert mw.get_client_id is not None  # per-client keying wired


def test_build_defaults_burst_to_2x_rate(monkeypatch, valid_base_env):
    """No explicit burst → FastMCP default of 2x. Mirrors lib behaviour."""
    monkeypatch.setenv("RATE_LIMITER_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMITER_MAX_REQUESTS_PER_SECOND", "7")
    monkeypatch.delenv("RATE_LIMITER_BURST_CAPACITY", raising=False)
    from config import Settings

    mw = build_rate_limit_middleware(Settings())
    assert mw is not None
    assert mw.burst_capacity == 14


def test_build_global_mode_propagates(monkeypatch, valid_base_env):
    monkeypatch.setenv("RATE_LIMITER_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMITER_GLOBAL", "true")
    from config import Settings

    mw = build_rate_limit_middleware(Settings())
    assert mw is not None
    assert mw.global_limit is True


def test_defaults_match_legacy_600_per_minute_budget(valid_base_env):
    """Default config = 10 req/s sustained, 20-token burst = legacy 600/60s."""
    from config import Settings

    s = Settings()
    assert s.rate_limiter_enabled is True
    assert s.rate_limiter_max_requests_per_second == 10.0
    assert s.rate_limiter_burst_capacity is None
    assert s.rate_limiter_global is False
    assert s.rate_limiter_trusted_proxy_hops == 1
