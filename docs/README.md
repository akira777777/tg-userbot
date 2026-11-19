# Документация tg-userbot

Инструмент для **личного аккаунта Telegram**: сбор участников групп, рассылки, личные
сообщения, оркестр из нескольких аккаунтов и контрольная плоскость Telegram Mini App.
Точка входа — [`../README.md`](../README.md); полный справочник команд —
[`../РУКОВОДСТВО.md`](../РУКОВОДСТВО.md).

## С чего начать

1. Установка и первый запуск — [`GUIDE.md`](GUIDE.md).
2. Полный справочник команд и сценариев — [`../РУКОВОДСТВО.md`](../РУКОВОДСТВО.md).
3. Как устроена система и где проходят границы доверия —
   [`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md).

## Карта документов

| Документ | О чём |
| --- | --- |
| [`GUIDE.md`](GUIDE.md) | Короткий операторский путь: установка, вход, первые dry-run. |
| [`../РУКОВОДСТВО.md`](../РУКОВОДСТВО.md) | Полный справочник CLI, сценарии, БД, ограничения Telegram, траблшутинг. |
| [`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md) | Runtime-модель, ответственность модулей, границы доверия, конкурентность, деплой. |
| [`api/README.md`](api/README.md) | Маршруты Mini App, контракт команд, воркер, локальная панель. |
| [`media/README.md`](media/README.md) | Медиа-библиотека поверх «Избранного»: `bot.py media`, кэш, веб-плеер. |
| [`telegram/README.md`](telegram/README.md) | Telethon-сессии, Bot API контроллер, `initData`, лимиты и риски. |
| [`plan/mini-app-control-plane.md`](plan/mini-app-control-plane.md) | Проектные решения Mini App и что ещё не сделано. |
| [`contributing/CONTRIBUTING.md`](contributing/CONTRIBUTING.md) | Локальные проверки, стиль, правила PR. |
| [`notes/endless-conduct.md`](notes/endless-conduct.md) | Заметка о режиме «бесконечной» кампании без лимита времени. |

## Что где лежит

```text
bot.py              userbot CLI (Telethon, SQLite)
cloud.py            зеркало в Supabase
web.py              локальная панель 127.0.0.1:8642
control/            Mini App: модели, политика, очередь, воркер, исполнитель, бот-контроллер
dashboard/          Next.js Mini App (деплой отдельно)
supabase/schema.sql зеркало + очередь команд + RLS
scripts/            healthcheck, backup, миграции, ручные утилиты
scripts/manual/     живые smoke-скрипты (нужна запущенная панель)
tests/              pytest без сети и без боевых файлов
docs/               эта документация
```

## Чего в проекте нет

В репозитории **нет** FastAPI-сервиса, Redis-кеша и отдельной PostgreSQL-базы
приложения. Источник правды — локальный SQLite и файлы сессий Telethon; Supabase —
уменьшенное зеркало и транспорт команд. Если в каком-то документе написано иначе,
документ устарел.

## Проверки

```bash
python -m pytest tests/
python -m ruff check .
cd dashboard && npm ci && npm test -- --run && npm run build
```
