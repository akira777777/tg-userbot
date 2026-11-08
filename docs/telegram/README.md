# Telegram-интеграция

> Документация по интеграции tg-userbot с Telegram.

## Архитектура

```
telegram/
├── client.py       # Telegram Bot API
├── handlers/       # обработчики команд
│   ├── messages.py # обработка сообщений
│   └── media.py    # обработка медиа
├── filters/        # фильтры
└── middlewares/    # middleware
```

## Установка

```bash
pip install tg-userbot[telegram]
```

## Базовый пример

```python
from tg_userbot.telegram.client import TelegramClient

async def main():
    bot = TelegramClient(
        token="123456:ABC...XYZ",
        bot_id=123456789,
    )

    await bot.run()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## Команды

### `/media`

```python
from tg_userbot.telegram.handlers import MediaHandler

media_handler = MediaHandler(
    library=media_library,  # MediaLibrary
)

bot.add_handler(media_handler)
```

### `/search`

```python
from tg_userbot.telegram.handlers import SearchHandler

search_handler = SearchHandler(
    library=media_library,
    search_engine=media_library.search,
)

bot.add_handler(search_handler)
```

### `/upload`

```python
from tg_userbot.telegram.handlers import UploadHandler

upload_handler = UploadHandler(
    library=media_library,
    storage=local_storage,
)

bot.add_handler(upload_handler)
```

## Фильтры

### По категориям

```python
from tg_userbot.telegram.filters import CategoryFilter

category_filter = CategoryFilter(
    categories=["music", "video", "audio"],
)

bot.add_filter(category_filter)
```

### По тегам

```python
from tg_userbot.telegram.filters import TagFilter

tag_filter = TagFilter(
    tags=["lofi", "beats", "study"],
)

bot.add_filter(tag_filter)
```

### По диапазонам

```python
from tg_userbot.telegram.filters import RangeFilter

range_filter = RangeFilter(
    min_duration=60,
    max_duration=300,
)

bot.add_filter(range_filter)
```

## Middleware

### Логирование

```python
from tg_userbot.telegram.middlewares import LoggingMiddleware

logging_middleware = LoggingMiddleware(
    logger=logger,
    level="INFO",
)

bot.add_middleware(logging_middleware)
```

### Rate limiting

```python
from tg_userbot.telegram.middlewares import RateLimitMiddleware

rate_limit_middleware = RateLimitMiddleware(
    rate=1.0,  # 1 запрос в секунду
    burst=10,
)

bot.add_middleware(rate_limit_middleware)
```

### Аутентификация

```python
from tg_userbot.telegram.middlewares import AuthMiddleware

auth_middleware = AuthMiddleware(
    users=[123456, 789012],  # список user_id
)

bot.add_middleware(auth_middleware)
```

## Обработка ошибок

```python
from tg_userbot.telegram.exceptions import (
    MediaNotFoundError,
    UploadError,
    SearchError,
)

from tg_userbot.telegram.handlers import ErrorHandler

error_handler = ErrorHandler(
    on_media_not_found=lambda: send_message("Медиа не найдено"),
    on_upload_error=lambda e: send_message(f"Ошибка загрузки: {e}"),
    on_search_error=lambda e: send_message(f"Ошибка поиска: {e}"),
)

bot.add_handler(error_handler)
```

## Тесты

```bash
pytest tests/test_telegram/ -v --cov=tg_userbot/telegram
```

## FAQ

### Вопрос 1: Как добавить поддержку нескольких ботов?

```python
from tg_userbot.telegram.client import MultiBotClient

client = MultiBotClient(
    bots=[
        TelegramClient(token="token1", bot_id=123),
        TelegramClient(token="token2", bot_id=456),
    ],
)
```

### Вопрос 2: Как реализовать Webhook-режим?

```python
from tg_userbot.telegram.client import WebhookClient

client = WebhookClient(
    token="123456:ABC...XYZ",
    webhook_url="https://your-domain.com/webhook",
)
```

### Вопрос 3: Как масштабировать бота?

Используй Docker Compose с автоскейлингом:

```yaml
telegram-webhook:
  image: python:3.12-slim-bookworm
  deploy:
    replicas: 2
  volumes:
    - ./src:/app/src:ro
```

## Вклад в развитие

1. Fork репозиторий
2. Создай ветку (`git checkout -b feature/amazing-feature`)
3. Запуш (`git push origin feature/amazing-feature`)
4. Открой PR

## Лицензия

MIT © Novra
