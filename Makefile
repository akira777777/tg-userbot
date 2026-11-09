.PHONY: help install test lint format run worker controller healthcheck docker-build docker-up docker-down clean

PYTHON ?= python
SOURCES = bot.py cloud.py web.py control tests scripts/healthcheck.py

help:
	@echo "tg-userbot targets: install test lint format run worker controller healthcheck docker-build docker-up docker-down"

install:
	$(PYTHON) -m pip install -e ".[dev]"

run:
	$(PYTHON) bot.py --help

worker:
	$(PYTHON) -m control.main worker

controller:
	$(PYTHON) -m control.main telegram-bot

healthcheck:
	$(PYTHON) scripts/healthcheck.py --db userbot.db

test:
	$(PYTHON) -m pytest tests/
	cd dashboard && npm test -- --run

lint:
	$(PYTHON) -m ruff check $(SOURCES)
	$(PYTHON) -m bandit -q -ll -r bot.py cloud.py web.py control scripts/healthcheck.py

format:
	$(PYTHON) -m black $(SOURCES)
	$(PYTHON) -m ruff check --fix $(SOURCES)

docker-build:
	docker build --target prod -t tg-userbot:latest .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf dist build htmlcov .coverage .pytest_cache .ruff_cache *.egg-info
