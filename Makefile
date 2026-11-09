# Реальные цели: bot.py, web.py, pytest, dashboard.
# Не вызывает FastAPI, gunicorn, alembic и несуществующий пакет src.tg_userbot.
PYTHON ?= .venv/Scripts/python.exe
ifeq ($(OS),Windows_NT)
  PYTHON := .venv/Scripts/python.exe
else
  PYTHON := .venv/bin/python
endif

.PHONY: help status web test dashboard-test

help:
	@echo tg-userbot
	@echo   make status          — bot.py status (только база)
	@echo   make web             — панель web.py
	@echo   make test            — pytest (без живой панели и Telegram)
	@echo   make dashboard-test  — vitest дашборда
	@echo Запуск: $(PYTHON) bot.py <команда>
	@echo Docker: docker compose -f docker-compose.yml up web
	@echo Не используйте docker-compose.yml старого FastAPI-стека — его больше нет.

status:
	$(PYTHON) bot.py status

web:
	$(PYTHON) web.py

test:
	$(PYTHON) -m pytest -q --tb=short -p no:cacheprovider

dashboard-test:
	npm --prefix dashboard test
