# tg-userbot runtime image: Telethon CLI + trusted control worker/controller.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system userbot && adduser --system --ingroup userbot userbot

COPY pyproject.toml README.md ./
COPY bot.py cloud.py web.py ./
COPY control ./control
COPY scripts ./scripts
RUN python -m pip install --upgrade pip && python -m pip install .

RUN mkdir -p /app/data /app/sessions /app/exports && \
    chown -R userbot:userbot /app

USER userbot
ENV USERBOT_DB=/app/data/userbot.db \
    USERBOT_SESSIONS=/app/sessions

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "scripts/healthcheck.py", "--db", "/app/data/userbot.db"]

FROM base AS dev
USER root
RUN python -m pip install ".[dev]"
USER userbot
CMD ["python", "-m", "control.main", "healthcheck"]

FROM base AS prod
CMD ["python", "-m", "control.main", "worker"]
