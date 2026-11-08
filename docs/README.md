# tg-userbot — Документация

> Telegram Userbot с медиа-библиотекой, поддержкой нескольких платформ и React-панелью.

## 📑 Содержание

- [Введение](#введение)
- [Аーキтектура](#архітектура)
- [Установка](#установка)
- [Запуск](#запуск)
- [API](#api)
- [Dashboard](#dashboard)
- [Медиа-библиотека](#медиа-библиотека)
- [Telegram-интеграция](#telegram-интеграция)
- [Contributing](#contributing)
- [Лицензия](#лицензия)

---

## Введение

`tg-userbot` — это Telegram Userbot, который предоставляет:

- **Медиа-библиотеку** — хранение, поиск и каталогизация медиа-файлов
- **Мультиплатформенную поддержку** — Telegram, VK, Twitter (X)
- **HTTP API** — RESTful API для интеграции с внешними системами
- **React-дашборд** — веб-интерфейс для управления медиа-библиотекой

## Архитектура

```
tg-userbot/
├── src/
│   ├── app/          # конфигурация, логирование, middleware
│   ├── handlers/     # обработчики команд
│   ├── media/        # медиа-библиотека
│   ├── database/     # интеграция с Supabase
│   ├── telegram/     # клиенты (Telegram, VK, Twitter)
│   └── api/          # HTTP API (FastAPI)
├── dashboard/        # React-панель
├── tests/            # тесты
├── scripts/          # утилиты (миграция, бэкапы)
└── docker-compose.yml
```

## Установка

### Требования

- Python 3.12+
- PostgreSQL 16
- Redis 7
- Node.js 18+ (для dashboard)

### Пошагово

```bash
# 1. Клонируем репозиторий
git clone https://github.com/novra/tg-userbot.git
cd tg-userbot

# 2. Создаём виртуальное окружение
python -m venv .venv
source .venv/bin/activate  # или .venv\Scripts\activate на Windows

# 3. Устанавливаем зависимости
pip install -e ".[dev]"

# 4. Создаём .env
cp .env.example .env
nano .env  # заполняем переменные окружения

# 5. Запускаем миграцию БД
alembic upgrade head

# 6. Заполняем тестовыми данными (опционально)
python scripts/db-seed.py

# 7. Запускаем приложение
fastapi dev src/app/main:app
```

## Запуск

### Docker (рекомендуется)

```bash
# Запуск всех сервисов (app + db + redis + prometheus + grafana)
docker-compose -f docker-compose.yml up -d

# Проверка статуса
docker-compose ps

# Логи
docker-compose logs -f app
```

### Docker Compose (продакшен)

```bash
docker-compose -f docker-compose.prod.yml up -d
```

### Docker (прямой запуск)

```bash
docker build -t tg-userbot:latest .
docker run -d \
  -p 8000:8000 \
  -e TG_BOT_TOKEN=your_token \
  -e SUPABASE_URL=your_url \
  -e SUPABASE_ANON_KEY=your_key \
  tg-userbot:latest
```

## API

API описано в OpenAPI-документации:

```bash
# Запуск отладочного сервера
fastapi dev src/app/main:app

# Открыть Swagger UI
# http://localhost:8000/docs
```

### `/api/media`

```http
GET /api/media
GET /api/media/:id
POST /api/media
PUT /api/media/:id
DELETE /api/media/:id
```

### `/api/media/search`

```http
POST /api/media/search
Content-Type: application/json

{
  "query": "lofi beats",
  "limit": 20,
  "offset": 0
}
```

### `/api/media/upload`

```http
POST /api/media/upload
Content-Type: multipart/form-data

file: <binary>
caption: "lofi beats"
tags: ["lofi", "beats"]
```

### `/api/media/metadata`

```http
POST /api/media/metadata
Content-Type: application/json

{
  "media_id": 123,
  "artist": "Chill Cow",
  "album": "LoFi Study",
  "year": 2024
}
```

## Dashboard

React-панель для управления медиа-библиотекой:

- Просмотр медиатеки
- Поиск и фильтрация
- Категории, теги, коллекции
- Статистика и аналитика

```bash
# Запуск dev-сервера для dashboard
cd dashboard
npm install
npm run dev
```

Открыть `http://localhost:3000`

## Медиа-библиотека

### Команды

| Команда | Описание |
|---------|----------|
| `media add` | Добавить медиа-файл в библиотеку |
| `media pull` | Скачать медиа-файл |
| `media sync` | Синхронизация с облачным зеркалом |
| `media del` | Удалить медиа-файл |
| `media list` | Список медиа-файлов |

### Пример: добавление медиа

```bash
media add --url "https://cdn.example.com/video.mp4" --title "Lofi Beats" --tags "lofi,beats"
```

### Пример: поиск

```bash
media search --query "lofi" --limit 20
```

## Telegram-интеграция

### Настройка бота

1. Создать бота через `@BotFather`
2. Скопировать токен в `.env`:
   ```
   TG_BOT_TOKEN=123456:ABC...
   ```
3. Запустить бота:
   ```bash
   TG_BOT_TOKEN=$(cat .env | grep TG_BOT_TOKEN | cut -d'=' -f2) \
   poetry run tg-userbot
   ```

### Команды бота

| Команда | Описание |
|---------|----------|
| `/media` | Показать медиатеку |
| `/search` | Поиск по медиатеке |
| `/upload` | Загрузить медиа |
| `/stats` | Статистика |

## Contributing

1. Fork репозиторий
2. Создай ветку (`git checkout -b feature/amazing-feature`)
3. Запуш (`git push origin feature/amazing-feature`)
4. Открой PR

### Разработчик-стек

```bash
make install       # pip install -e ".[dev]"
make dev           # fastapi dev src/app/main:app
make test          # pytest tests/ -v --cov
make lint          # ruff check src/ tests/
make format        # black src/ tests/ ruff format src/ tests/
make docs          # mkdocs serve
```

### Commit-конвенция

```
feat: новая фича
fix: исправление бага
docs: документация
refactor: рефакторинг
test: тесты
chore: рутина
```

## Лицензия

MIT © Novra
