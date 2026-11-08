# Медиа-библиотека

> Документация по медиа-библиотеке tg-userbot.

## Введение

Медиа-библиотека — это модуль для хранения, поиска и каталогизации медиа-файлов (видео, аудио, изображения).

## Архитектура

```
media/
├── library.py      # основная логика (CRUD, поиск, фильтрация)
├── collection.py   # коллекции медиа
├── metadata.py     # метаданные (артист, альбом, жанр)
├── upload.py       # загрузка файлов
└── download.py     # скачивание файлов
```

## Установка

```bash
pip install tg-userbot[media]
```

## Примеры

### Создание библиотеки

```python
from tg_userbot.media import MediaLibrary

library = MediaLibrary(
    storage_path="./media",
    index_path="./index.db",
    max_size_mb=10240,  # 10 ГБ
)
```

### Добавление файла

```python
import asyncio
from tg_userbot.media.library import MediaLibrary

async def main():
    library = MediaLibrary(storage_path="./media")

    await library.add(
        url="https://cdn.example.com/video.mp4",
        title="Lofi Beats #1",
        description="Чилл-бит для работы и учёбы",
        category="music",
        tags=["lofi", "beats", "study"],
        thumbnail="https://cdn.example.com/thumbs/1.jpg",
    )

    await library.commit()

asyncio.run(main())
```

### Поиск

```python
from tg_userbot.media.library import MediaLibrary

async def main():
    library = MediaLibrary(storage_path="./media")

    results = await library.search(
        query="lofi",
        limit=20,
        offset=0,
        category="music",
        tags=["lofi", "beats"],
        sort="views",
        order="desc",
    )

    for item in results:
        print(item.title, item.views)

asyncio.run(main())
```

### Коллекции

```python
from tg_userbot.media.collection import Collection

collection = Collection(
    name="Lofi Beats",
    description="Лучшие лофи-биты для работы",
    cover_url="https://cdn.example.com/collections/lofi.jpg",
    is_featured=True,
)

collection.media.append(media_item)
await collection.save()
```

### Метаданные

```python
from tg_userbot.media.metadata import Metadata

metadata = Metadata(
    media_id=1,
    artist="Chill Cow",
    album="LoFi Study",
    year=2024,
    genre="lofi hip hop",
    bpm=85,
)

await metadata.save()
```

## Конфигурация

```python
from tg_userbot.media.config import MediaConfig

config = MediaConfig(
    storage_path="./media",
    index_path="./index.db",
    max_size_mb=10240,
    thumbnail_size=(128, 72),
    cache_ttl_seconds=3600,
    rate_limit_per_minute=60,
)
```

## Производительность

- **Поиск**: векторный поиск с `chroma` (0.1–2 мс на запрос)
- **Пагинация**: cursor-based (для больших датасетов)
- **Кеширование**: Redis (TTL: 1 час)
- **Индексы**: SQLite (для малых датасетов), PostgreSQL (для больших)

## Мониторинг

```python
from tg_userbot.media.metrics import Metrics

metrics = Metrics()
metrics.track("media.added", tags={"category": "music"})
metrics.track("media.searched", tags={"query": "lofi"})
metrics.track("media.failed", tags={"error": "timeout"})
```

## Тесты

```bash
pytest tests/test_media/ -v --cov=tg_userbot/media
```

## FAQ

### Вопрос 1: Как увеличить размер хранилища?

```python
config = MediaConfig(max_size_mb=102400)  # 100 ГБ
```

### Вопрос 2: Как добавить поддержку новых форматов?

```python
from tg_userbot.media.upload import UploadStrategy

class MP4Strategy(UploadStrategy):
    async def process(self, file: Path) -> str:
        # конвертация в mp4
        pass
```

### Вопрос 3: Как реализовать автореференцию?

```python
from tg_userbot.media.library import MediaLibrary

library = MediaLibrary(
    storage_path="./media",
    auto_sync_interval=3600,  # каждые 1 час
)
```

## Вклад в развитие

1. Fork репозиторий
2. Создай ветку (`git checkout -b feature/amazing-feature`)
3. Запуш (`git push origin feature/amazing-feature`)
4. Открой PR

## Лицензия

MIT © Novra
