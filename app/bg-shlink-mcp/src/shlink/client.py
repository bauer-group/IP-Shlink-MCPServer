"""
Shlink HTTP client - thin async wrapper around httpx.AsyncClient.

Single responsibility: add the X-Api-Key header, apply bounded retries on
transient failures, and translate Shlink's RFC 7807 problem+json into our
typed exception hierarchy. Everything tool-related lives in tool_mapper.py.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from .errors import (
    ShlinkError,
    ShlinkServerError,
    ShlinkTransportError,
    from_status,
)


# Status codes that are worth retrying. 408/425/429 are transient on the
# caller's side; 500/502/503/504 are transient on Shlink's side.
RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.25  # seconds
DEFAULT_BACKOFF_MAX = 4.0  # seconds


class ShlinkClient:
    """
    Async client for Shlink REST v3.

    Wraps a long-lived httpx.AsyncClient. Use as an async context manager,
    or hand the underlying `httpx_client` directly to FastMCP.from_openapi().
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        user_agent: str = "bg-shlink-mcp/0.1.0",
    ) -> None:
        if not api_key:
            raise ValueError("ShlinkClient requires a non-empty api_key")

        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

        self.httpx_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
            # Connection pooling: enough for a single MCP container under load,
            # not so high that we DDoS the Shlink instance.
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
            follow_redirects=False,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def __aenter__(self) -> "ShlinkClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.httpx_client.aclose()

    # ── Convenience verbs ──────────────────────────────────────────────────

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)

    # ── Core request loop ──────────────────────────────────────────────────

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """
        Send a request with retry + error mapping.

        Raises a `ShlinkError` subclass on non-2xx responses. Returns the
        httpx.Response on success - call `.json()` on it as usual.
        """
        attempt = 0
        last_exc: Exception | None = None

        while True:
            attempt += 1
            try:
                response = await self.httpx_client.request(method, path, **kwargs)
            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.PoolTimeout) as exc:
                last_exc = exc
                if attempt > self._max_retries:
                    raise ShlinkTransportError(
                        f"Network failure talking to Shlink after {attempt - 1} retries: {exc}"
                    ) from exc
                await self._sleep_backoff(attempt)
                continue
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt > self._max_retries:
                    raise ShlinkTransportError(
                        f"Shlink timed out after {attempt - 1} retries: {exc}"
                    ) from exc
                await self._sleep_backoff(attempt)
                continue

            # Success path
            if 200 <= response.status_code < 300:
                return response

            # Retryable error?
            if response.status_code in RETRYABLE_STATUSES and attempt <= self._max_retries:
                await self._sleep_backoff(attempt, response=response)
                continue

            # Permanent failure - map to typed exception
            raise self._map_error(response)

    # ── Internals ──────────────────────────────────────────────────────────

    def _map_error(self, response: httpx.Response) -> ShlinkError:
        body: dict[str, Any] = {}
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    body = parsed
            except ValueError:
                pass
        return from_status(response.status_code, body=body)

    async def _sleep_backoff(self, attempt: int, *, response: httpx.Response | None = None) -> None:
        """Exponential backoff with full jitter; honours Retry-After when present."""
        retry_after: float | None = None
        if response is not None:
            raw = response.headers.get("retry-after")
            if raw:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None

        if retry_after is not None:
            delay = min(retry_after, self._backoff_max)
        else:
            exp = self._backoff_base * (2 ** (attempt - 1))
            delay = min(exp, self._backoff_max)
            # Full jitter avoids retry storms when many tools fire at once.
            delay = random.uniform(0.0, delay)

        await asyncio.sleep(delay)

    # ── Domain helpers ─────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """
        Call /health and return the parsed body.

        Used by the container HEALTHCHECK and by the MCP `shlink://health` resource.
        Never retries - a single 503 should fail fast so Docker can restart us.
        """
        try:
            response = await self.httpx_client.get("/health")
        except httpx.HTTPError as exc:
            raise ShlinkTransportError(f"Shlink health probe failed: {exc}") from exc
        if response.status_code >= 500:
            raise ShlinkServerError(
                f"Shlink reported unhealthy: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ShlinkError(f"Shlink /health returned non-JSON: {response.text[:200]}") from exc


def build_shlink_client_from_settings(settings: Any) -> ShlinkClient:
    """
    Factory that pulls the API key out of SecretStr and wires up the client.

    Kept separate from __init__ so tests can construct ShlinkClient directly
    with a stub key, while production code always routes through Settings.
    """
    return ShlinkClient(
        base_url=settings.shlink_api_base,
        api_key=settings.shlink_api_key.get_secret_value(),
        timeout=float(settings.shlink_http_timeout),
    )
