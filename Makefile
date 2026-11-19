.PHONY: help install test lint format run worker controller healthcheck docker-build docker-up docker-down clean

PYTHON ?= python
SOURCES = bot.py cloud.py web.py control tests scripts

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
	$(PYTHON) -m pytest tests/ --cov=control --cov=bot --cov=cloud --cov-report=term-missing
	cd dashboard && npm test -- --run

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m bandit -q -ll -r bot.py cloud.py web.py control scripts

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
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('dist','build','htmlcov','.pytest_cache','.ruff_cache')]; import glob, os; [os.remove(f) for f in glob.glob('*.egg-info') + ['.coverage'] if os.path.exists(f)]"
