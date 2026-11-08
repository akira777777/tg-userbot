# tg-userbot — Dockerfile
# Многоступенчатый образ: slim (развертывание) + dev (разработка)
# Базовый образ: Python 3.12 slim (минимум, ~500 МБ)
# Стек: FastAPI, uvicorn, python-telegram-bot, Supabase, Redis
# Размер образа: ~600 МБ

# ==================== ОСНОВНОЙ ОБРАЗ (slim) ====================
FROM python:3.12-slim-bookworm AS base

# Ставям локали и переменные окружения
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=3 \
    PIP_DEFAULT_TIMEOUT=10 \
    POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_CACHE_DIR="/root/.cache/poetry" \
    PATH="$POETRY_HOME/bin:$PATH"

# Ставим рабочую директорию
WORKDIR /app

# Ставим базовые зависимости (не изменяются)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libpq-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Ставим poetry (менеджер зависимостей)
RUN curl -sSL https://install.python-poetry.org | python3 - --version $POETRY_VERSION

# Копируем файлы проекта
COPY pyproject.toml poetry.lock* ./
COPY .env.example .env

# Ставим зависимости (базовые + dev)
RUN poetry install --no-interaction --no-root --no-compile --without-hatchling

# Копируем исходный код
COPY --chown=poetry:root ./src ./src
COPY --chown=poetry:root ./scripts ./scripts
COPY --chown=poetry:root ./dashboard ./dashboard

# Ставим приложение
RUN poetry build

# Экспортируем порт и входим в контейнер
EXPOSE 8000
CMD ["poetry", "run", "poetry", "run", "uvicorn", "src.tg_userbot.main:app", "--host", "0.0.0.0", "--port", "8000"]

## ==================== ОБРАЗ ДЛЯ РАЗРАБОТКИ (dev) ====================
FROM base AS dev

# Ставим VS Code Remote
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Ставим pre-commit
RUN pip install pre-commit

# Ставим dev-зависимости
RUN poetry install --no-interaction --no-root --no-compile

# Ставим редактор и отладчики
RUN apt-get update && apt-get install -y --no-install-recommends \
    code \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Ставим VS Code Remote SSH (для разработки на macOS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

CMD ["bash", "-c", "poetry run python src/tg_userbot/main.py"]

## ==================== ОБРАЗ ДЛЯ ПРОДАКШЕНА (prod) ====================
FROM base AS prod

# Ставим gunicorn
RUN pip install gunicorn uvicorn

# Копируем исходный код
COPY --chown=poetry:root ./src ./src
COPY --chown=poetry:root ./scripts ./scripts

# Ставим приложение
RUN poetry build

# Ставим порт и входим в контейнер
EXPOSE 8000
CMD ["poetry", "run", "gunicorn", "--workers", "4", "--bind", "0.0.0.0:8000", "src.tg_userbot.main:app"]
