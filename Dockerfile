# ==============================================================================
# Telegram Userbot — Production Multi-Stage Dockerfile
# Base OS: Debian 12 (Bookworm) Slim
# Python: 3.12
# Architecture: Multi-stage (builder -> runner)
# Security: Non-root user (appuser, UID 1000), isolated virtualenv, no compilers
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build & Dependency Compilation
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Prevent debconf interactive prompts and disable Python bytecode/buffering
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

# Install build-time toolchain and C-header dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for clean isolation
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade core packaging tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install production Python dependencies
WORKDIR /build
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Minimal Hardened Runtime
# ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runner

# Production runtime environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    USERBOT_DB=/app/data/userbot.db \
    USERBOT_SESSIONS=/app/sessions \
    USERBOT_MEDIA=/app/media_cache \
    WEBBOT_PORT=8642 \
    WEBBOT_HOST=0.0.0.0

# Install runtime OS utilities:
# - ca-certificates: TLS verification for Telegram MTProto, Bot API, and Supabase
# - tzdata: Accurate timezone calculations
# - curl: Container healthchecks
# - tini: Signal forwarding and zombie process reaping (PID 1)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user (UID 1000 / GID 1000)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/sh -d /home/appuser -m appuser

# Set working directory
WORKDIR /app

# Pre-create required runtime directories with ownership
RUN mkdir -p /app/data /app/sessions /app/media_cache /app/logs && \
    chown -R appuser:appuser /app

# Copy isolated virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Create entrypoint wrapper script with POSIX line endings
RUN printf '#!/bin/sh\nset -e\nmkdir -p /app/data /app/sessions /app/media_cache /app/logs 2>/dev/null || true\ncase "$1" in\n  worker)\n    exec python -m control.main worker\n    ;;\n  controller|telegram-bot)\n    exec python -m control.main telegram-bot\n    ;;\n  web)\n    exec python web.py\n    ;;\n  healthcheck)\n    exec python -m control.main healthcheck\n    ;;\n  bot)\n    shift\n    exec python bot.py "$@"\n    ;;\n  *)\n    exec "$@"\n    ;;\nesac\n' > /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

# Copy application source code and assets with explicit file boundaries
COPY --chown=appuser:appuser bot.py web.py cloud.py pyproject.toml requirements.txt ./
COPY --chown=appuser:appuser control/ ./control/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser static/ ./static/

# Declare mount targets for persistent volumes
VOLUME ["/app/data", "/app/sessions", "/app/media_cache", "/app/logs"]

# Drop privileges to non-root user
USER appuser

# Expose web dashboard port
EXPOSE 8642

# Define init process and default entrypoint
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]

# Default command: background queue worker
CMD ["worker"]
