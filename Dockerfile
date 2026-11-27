# tg-userbot runtime image: Telethon CLI + trusted control worker/controller.
FROM python:3.12-slim-bookworm AS build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
RUN python -m venv /opt/venv

# Install dependencies before app sources to preserve the dependency cache.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY bot.py cloud.py web.py ./
COPY control ./control
COPY tguserbot ./tguserbot
COPY scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps .

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    USERBOT_DB=/app/data/userbot.db \
    USERBOT_SESSIONS=/app/sessions

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/scripts ./scripts

RUN addgroup --system userbot && adduser --system --ingroup userbot userbot && \
    mkdir -p /app/data /app/sessions /app/exports && \
    chown -R userbot:userbot /app

USER userbot

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "scripts/healthcheck.py", "--db", "/app/data/userbot.db"]

FROM base AS dev
USER root
COPY pyproject.toml README.md ./
COPY bot.py cloud.py web.py ./
COPY control ./control
COPY tguserbot ./tguserbot
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install ".[dev]"
USER userbot
CMD ["python", "-m", "control.main", "healthcheck"]

FROM base AS prod
CMD ["python", "-m", "control.main", "worker"]
