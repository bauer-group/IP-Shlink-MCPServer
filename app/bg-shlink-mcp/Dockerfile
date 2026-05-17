# =============================================================================
# bg-shlink-mcp — BAUER GROUP Shlink MCP Server
# =============================================================================
# Remote MCP bridge for self-hosted Shlink. OAuth 2.1 (Entra / Google / OIDC)
# inbound, X-Api-Key outbound, FastMCP + streamable-http transport.
#
# Multi-stage build with integrated testing.
#
#   docker build --target prod -t bg-shlink-mcp .         # production only
#   docker build --target test -t bg-shlink-mcp-test .    # run tests
#   docker build -t bg-shlink-mcp .                       # full pipeline (test gates prod)
# =============================================================================

# ── Stage 1: Builder — install Python dependencies into a relocatable prefix ──
FROM python:3.14-alpine AS builder

RUN apk add --no-cache build-base libffi-dev

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install into /install so the prod stage can copy a clean, isolated tree.
# `--no-deps` is intentionally NOT used - we want transitive deps included.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefix=/install .


# ── Stage 2: Test — run pytest; image build fails on any test failure ────────
FROM python:3.14-alpine AS test

RUN apk add --no-cache build-base libffi-dev

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY tests/ ./tests/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir ".[test]"

ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# `env -i` wipes CI build args that would otherwise leak into Pydantic Settings
# during test-time validation (e.g. a GitHub Actions runner exporting
# SHLINK_API_KEY into the shell).
RUN env -i HOME="$HOME" PATH="$PATH" PYTHONPATH="$PYTHONPATH" \
        PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
        pytest tests/ -q --tb=short


# ── Stage 3: Production — minimal runtime image ──────────────────────────────
FROM python:3.14-alpine AS prod

# ── OCI / BAUER GROUP labels ────────────────────────────────────────────────
LABEL vendor="BAUER GROUP"
LABEL maintainer="Karl Bauer <karl.bauer@bauer-group.com>"

LABEL org.opencontainers.image.title="bg-shlink-mcp — BAUER GROUP Shlink MCP Server"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.authors="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/IP-Shlink-MCPServer"
LABEL org.opencontainers.image.url="https://github.com/bauer-group/IP-Shlink-MCPServer"
LABEL org.opencontainers.image.documentation="https://github.com/bauer-group/IP-Shlink-MCPServer#readme"
LABEL org.opencontainers.image.description="bg-shlink-mcp — OIDC-gated remote MCP for self-hosted Shlink (BAUER GROUP)"
LABEL com.bauer-group.image.component="mcp-server"
LABEL com.bauer-group.image.stack="shlink"
LABEL org.opencontainers.image.version="0.1.0"

RUN apk add --no-cache \
        tini \
        tzdata \
        ca-certificates \
        curl \
    && rm -rf /var/cache/apk/* \
    && addgroup -g 1001 -S mcp \
    && adduser -S -D -u 1001 -G mcp -h /home/mcp mcp

# Python deps from the builder stage (relocatable prefix install).
COPY --from=builder /install /usr/local

# Hard test-stage gate — if tests fail upstream, this COPY cannot resolve and
# the prod build fails. The file path is arbitrary; we use README.md because
# it's guaranteed to exist in the test stage.
COPY --from=test /app/README.md /tmp/_test_passed
RUN rm /tmp/_test_passed

WORKDIR /app
COPY --chown=mcp:mcp src/ ./src/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER mcp
EXPOSE 8000

# /healthz returns 200 OK as soon as the FastMCP lifespan reports ready.
# Distinct from Shlink's /health (which sits behind the OAuth wall).
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# tini as PID 1 forwards SIGTERM cleanly to the python process so `docker stop`
# triggers a graceful FastMCP lifespan shutdown instead of a 10s SIGKILL wait.
ENTRYPOINT ["/sbin/tini", "--", "python", "src/main.py"]
CMD ["serve"]


# -----------------------------------------------------------------------------
# Usage
# -----------------------------------------------------------------------------
# Default (serve):        docker run ... bg-shlink-mcp
# Health probe:           docker run ... bg-shlink-mcp health
# Print tool catalogue:   docker run ... bg-shlink-mcp tools
