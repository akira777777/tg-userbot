# Contributing

Коротко: маленькие изменения, проверяемые команды, никаких секретов в репозитории.

## Локальная среда

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
cd dashboard && npm ci
```

## Перед PR

```bash
python -m pytest tests/
python -m ruff check .
python -m py_compile bot.py web.py cloud.py
cd dashboard && npm test -- --run && npm run build
```

Опционально: `python -m bandit -q -ll -r bot.py cloud.py web.py control scripts`.

## Структура и правила

- `bot.py` — userbot CLI; бизнес-логика остаётся в нём, новые команды добавляются в
  `build_parser()` и получают тест.
- `control/` — контрольная плоскость: модели Pydantic, политика, очередь, воркер,
  исполнитель. Никакого сетевого I/O в политике и моделях.
- `dashboard/` — Next.js Mini App; серверные ключи только в серверных route-хендлерах.
- `tests/` — только pytest; тест не должен трогать `userbot.db`, `.env`, реальные
  сессии, Saved Messages, Supabase и сеть.
- `scripts/` — операторские утилиты; `scripts/manual/` — живые smoke-скрипты, которые
  запускают руками против работающей панели.
- Документация должна совпадать с кодом. Если меняете поведение — обновите
  `README.md`, `РУКОВОДСТВО.md` и профильный документ в `docs/`.

## Стиль

- Python 3.12+, `ruff` (line length 120, правила `E9`, `F`), `black` для форматирования.
- Публичные функции — с docstring; сообщения CLI — по-русски, как остальной интерфейс.
- Не добавляйте зависимости без необходимости; `pyproject.toml` — единственный источник
  зависимостей, `requirements.txt` — runtime-зеркало.

## Безопасность

- `.env`, `userbot.db`, `*.session*`, `account-secrets.json` — никогда в git и в логи.
- Не логируйте токены, `API_HASH`, пути сессий и полные списки получателей.
- Live-рассылки и `scrape` не запускаются для проверки кода.

## Коммиты и PR

Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
В PR опишите, что проверено (какие команды запускались) и какие ограничения остались.
