# Операторский quick start

Короткий путь для этой машины. Полная версия — [`../РУКОВОДСТВО.md`](../РУКОВОДСТВО.md).

## 1. Подготовка

```powershell
cd C:\Users\novra\Desktop\tg-userbot\tg-userbot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env   # вписать API_ID / API_HASH из my.telegram.org
```

## 2. Вход и база

```powershell
.\.venv\Scripts\python.exe bot.py login
.\.venv\Scripts\python.exe bot.py status
.\.venv\Scripts\python.exe bot.py groups --sync
```

## 3. Безопасная проверка рассылки

```powershell
.\.venv\Scripts\python.exe bot.py broadcast --text "Превью" --dry-run
.\.venv\Scripts\python.exe bot.py dmsend --file message.txt --limit 5 --dry-run
```

Dry-run печатает несколько отрендеренных вариантов (личные — с футером «стоп») и не
отправляет сообщения; для `dmsend` сессия Telegram не нужна. Живой запуск —
только после проверки текста, лимитов и прав аккаунта.

## 4. Панель и тесты

```powershell
.\.venv\Scripts\python.exe web.py          # http://127.0.0.1:8642
.\.venv\Scripts\python.exe -m pytest tests/
.\.venv\Scripts\python.exe -m ruff check .
```

## 5. Mini App (опционально)

Нужны `BOT_TOKEN`, `MINI_APP_URL`, `OWNER_TELEGRAM_IDS`, `CONTROL_SESSION_SECRET`,
`SUPABASE_URL`, `SUPABASE_SECRET_KEY` в `.env`, применённая схема
[`../supabase/schema.sql`](../supabase/schema.sql) и деплой `dashboard/`.

```powershell
.\.venv\Scripts\python.exe -m control main healthcheck
.\.venv\Scripts\python.exe -m control main worker
.\.venv\Scripts\python.exe -m control main telegram-bot
```

Проверить, что переменные заполнены (без вывода секретов):

```powershell
.\.venv\Scripts\python.exe scripts\env_check.py
```

## Правила

- Живые `broadcast` / `dmsend` / `conduct` / `scrape` для проверки кода не запускать.
- `.env`, `userbot.db`, `*.session` — секреты, вне git.
- Начинать с `--dry-run` и малых лимитов, следить за `bot.py status`.
