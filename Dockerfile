# Control-plane image. The public container does not embed session files,
# Bot API tokens, or a copy of the operator environment.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml README.md ./
COPY control ./control
COPY bot.py web.py cloud.py ./

RUN pip install --no-cache-dir .

FROM base AS dev

COPY tests ./tests
RUN pip install --no-cache-dir ".[dev]"
USER appuser
CMD ["pytest", "tests", "-q"]

FROM base AS prod

USER appuser
EXPOSE 8741
CMD ["python", "-m", "control.main", "healthcheck"]
