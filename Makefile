.PHONY: help install dev test lint format docs build run clean

## ============ ПОМОЩЬ ============
help:
	@echo "=== tg-userbot — Makefile ==="
	@echo ""
	@echo "Основные команды:"
	@echo "  make install     — установить зависимости (pip install -e '.[dev]')"
	@echo "  make dev         — проверка конфигурации контроллера (python -m control.main healthcheck)"
	@echo "  make test        — запуск тестов (pytest tests/ -v)"
	@echo "  make lint        — линтинг (ruff, black, mypy по control/ и tests/)"
	@echo "  make format      — форматирование (black control tests)"
	@echo "  make docs        — сборка документации (mkdocs serve)"
	@echo "  make build       — сборка проекта (python -m build)"
	@echo "  make run         — запуск в production (gunicorn src/tg_userbot.main:app)"
	@echo "  make clean       — очистка кэша (rm -rf .venv dist/ build/ .eggs/)"
	@echo ""
	@echo "Команды тестирования:"
	@echo "  make test-cov    — тесты с покрытием (pytest tests/ -v --cov)"
	@echo "  make test-fast   — быстрый тест (pytest tests/ -v -x -k 'fast')"
	@echo ""
	@echo "Команды разработки:"
	@echo "  make lint        — линтинг (ruff check src/)"
	@echo "  make format      — форматирование (black src/ ruff format src/)"
	@echo "  make docs        — документация (mkdocs serve)"
	@echo ""
	@echo "Команды деплоя:"
	@echo "  make docker-build    — сборка Docker-образа"
	@echo "  make docker-run      — запуск в Docker"
	@echo "  make docker-up       — запуск docker-compose"
	@echo "  make docker-down     — остановка docker-compose"
	@echo ""
	@echo "Команды БД:"
	@echo "  make db-up          — запуск БД (docker-compose -f docker-compose.db.yml up)"
	@echo "  make db-migrate     — миграция БД (alembic upgrade head)"
	@echo "  make db-seed        — заполнение тестовыми данными"
	@echo "  make db-clean       — сброс БД (alembic downgrade -1)"
	@echo ""
	@echo "Команды чистки:"
	@echo "  make clean          — очистка кэша (.venv, dist/, build/, .eggs/)"
	@echo "  make clean-all      — полная очистка (.venv, htmlcov/, .coverage, *.egg-info)"

## ============ ОСНОВНЫЕ КОМАНДЫ ============
install:
	pip install -e ".[dev]"

dev:
	python -m control.main healthcheck

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=control --cov-report=html --cov-report=term-missing

test-fast:
	pytest tests/ -v -x -k "fast"

lint:
	ruff check control tests
	black --check control tests
	mypy control

format:
	black control tests
	ruff check control tests --fix

docs:
	mkdocs serve

build:
	python -m build

run:
	python -m control.main healthcheck

clean:
	rm -rf .venv dist/ build/ .eggs/ *.egg-info

clean-all:
	rm -rf .venv htmlcov/ .coverage .pytest_cache/ __pycache__ */__pycache__ */*/__pycache__ dist/ build/ *.egg-info

## ============ DOCKER ============
docker-build:
	docker build -t tg-userbot:latest .

docker-run:
	docker run -d --name tg-userbot \
		-p 8000:8000 \
		-e TG_BOT_TOKEN=$(cat .env | grep TG_BOT_TOKEN | cut -d'=' -f2) \
		-e SUPABASE_URL=$(cat .env | grep SUPABASE_URL | cut -d'=' -f2) \
		-e SUPABASE_ANON_KEY=$(cat .env | grep SUPABASE_ANON_KEY | cut -d'=' -f2) \
		tg-userbot:latest

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

## ============ БАЗА ДАННЫХ ============
db-up:
	docker-compose -f docker-compose.db.yml up -d

db-migrate:
	alembic upgrade head

db-seed:
	python scripts/db-seed.py

db-clean:
	alembic downgrade -1

## ============ ВЕРСИОНирование ============
version:
	@echo "Версия проекта: $(shell git describe --tags --always --dirty=+)"
