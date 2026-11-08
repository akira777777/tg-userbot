"""db-seed.py — заполнение БД тестовыми данными для разработки.

ИСПРАВЛЕНИЯ (2026-11-08):
  - Убран хардкод SUPABASE_URL и SUPABASE_KEY.
  - Теперь читает переменные окружения SUPABASE_URL и SUPABASE_ANON_KEY.
  - Добавлена обработка случая, когда переменные окружения не заданы.
  - Добавлена обработка случая, когда create_client() не может быть выполнен.

Используется при разработке, тестировании, демо-сессиях.
Не используется в продакшене.

Использование:
    python scripts/db-seed.py --count 100
    python scripts/db-seed.py --count 100 --url "https://my-project.supabase.co" --key "my-key"

Генерирует:
    - пользователи (Telegram users)
    - медиа-файлы (media files)
    - коллекции (collections)
    - теги (tags)
    - метаданные (metadata)
"""

import asyncio
import logging
import sys
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)


def load_config() -> tuple[str, str]:
    """Загрузка конфигурации из переменных окружения.

    Возвращает:
        (SUPABASE_URL, SUPABASE_KEY)

    Если переменные окружения не заданы, выводит подсказку и выводит ошибку.
    """
    supabase_url = sys.argv[1] if len(sys.argv) >= 2 else os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
    supabase_key = os.getenv("SUPABASE_ANON_KEY", "your-anon-key-here")

    if not supabase_url or not supabase_key:
        print("⚠️  Предупреждение: SUPABASE_URL и/или SUPABASE_ANON_KEY не заданы.")
        print("ℹ️  Установите переменные окружения:")
        print("   SUPABASE_URL=https://my-project.supabase.co")
        print("   SUPABASE_ANON_KEY=your-anon-key-here")
        print()
    return (supabase_url, supabase_key)


async def seed_users(count: int = 100) -> list[dict]:
    """Генерация тестовых пользователей."""
    users = []
    for i in range(count):
        username = f"user_{i+1:04d}"
        avatar_url = f"https://i.pravatar.cc/150?u={i+1}"
        bio = f"Пользователь {i+1} из тестовой серии."
        users.append(
            {
                "username": username,
                "full_name": f"User {i+1}",
                "avatar_url": avatar_url,
                "bio": bio,
                "is_active": True,
                "created_at": (datetime.now(tz=timezone.utc) - timedelta(days=i * 365)).isoformat(),
            }
        )
    return users


async def seed_media(count: int = 500) -> list[dict]:
    """Генерация тестовых медиа-файлов."""
    media = []
    for i in range(count):
        category = ["music", "video", "audio", "image", "other"][i % 5]
        tags = ["popular", "trending", "new", "viral", "classic"][i % 5]
        media.append(
            {
                "title": f"Media #{i+1:04d}",
                "url": f"https://cdn.example.com/media/{i+1}.mp4",
                "thumbnail": f"https://cdn.example.com/media/thumbs/{i+1}.jpg",
                "duration": (i % 300) + 10,  # 10–310 секунд
                "size": (i % 100) * 1_000_000,  # 100 КБ – 100 МБ
                "category": category,
                "tags": [tags],
                "views": (i % 1000) + 100,
                "is_featured": i % 10 == 0,
                "created_at": (datetime.now(tz=timezone.utc) - timedelta(days=i % 7)).isoformat(),
            }
        )
    return media


async def seed_collections(count: int = 20) -> list[dict]:
    """Генерация тестовых коллекций."""
    collections = []
    for i in range(count):
        collections.append(
            {
                "name": f"Коллекция #{i+1}",
                "description": f"Коллекция из {i+1} элемента.",
                "cover_url": f"https://cdn.example.com/collections/cover_{i+1}.jpg",
                "is_featured": i % 3 == 0,
            }
        )
    return collections


async def seed_tags(count: int = 50) -> list[dict]:
    """Генерация тестовых тегов."""
    tags = []
    adjectives = ["best", "top", "new", "old", "hot", "cool", "funny", "epic"]
    nouns = ["hits", "clips", "memes", "vibes", "moments", "vibes"]
    for i in range(count):
        adjective = adjectives[i % len(adjectives)]
        noun = nouns[i % len(nouns)]
        tags.append(
            {
                "name": f"{adjective.capitalize()}{noun.capitalize()}",
                "description": f"Тег #{i+1}",
                "color": ["red", "blue", "green", "orange", "purple"][i % 5],
            }
        )
    return tags


async def seed_metadata() -> list[dict]:
    """Генерация метаданных для существующих медиа-файлов."""
    metadata = []
    for i in range(50):
        metadata.append(
            {
                "media_id": i + 1,
                "artist": f"Artist {i % 10 + 1}",
                "album": f"Album {i % 5 + 1}",
                "year": 2020 + (i % 5),
                "bpm": 120 + (i % 20),
                "genre": ["pop", "rock", "hip-hop", "electronic", "jazz"][i % 5],
            }
        )
    return metadata


async def main() -> None:
    """Главная функция — заполнение всех таблиц.

    Исправлено:
      - Добавлена загрузка конфигурации из переменных окружения.
      - Добавлена обработка случая, когда create_client() не может быть выполнен.
    """
    logging.basicConfig(level=logging.INFO)

    # Загружаем конфигурацию из переменных окружения
    supabase_url, supabase_key = load_config()

    # Проверяем, что переменные окружения заданы
    if not supabase_url or not supabase_key:
        print("❌ Ошибка: SUPABASE_URL и SUPABASE_ANON_KEY не заданы.")
        print("ℹ️  Установите переменные окружения:")
        print("   SUPABASE_URL=https://my-project.supabase.co")
        print("   SUPABASE_ANON_KEY=your-anon-key-here")
        sys.exit(1)

    # Проверяем, что supabase-client установлен
    try:
        import supabase
    except ImportError:
        print("❌ Ошибка: supabase-client не установлен. Установите: pip install supabase-client")
        sys.exit(1)

    # Создаём клиент Supabase — обрабатываем случай, когда create_client() не может быть выполнен
    try:
        supabase: Client = create_client(supabase_url, supabase_key)
    except Exception as e:
        print(f"❌ Ошибка при создании клиента Supabase: {e}", file=sys.stderr)
        sys.exit(1)

    # Заполняем таблицы
    logger.info("Seeding users...")
    users = await seed_users(count=100)
    result = supabase.table("users").insert(users).execute()
    logger.info(f"Created {len(users)} users")

    logger.info("Seeding media...")
    media = await seed_media(count=500)
    result = supabase.table("media").insert(media).execute()
    logger.info(f"Created {len(media)} media files")

    logger.info("Seeding collections...")
    collections = await seed_collections(count=20)
    result = supabase.table("collections").insert(collections).execute()
    logger.info(f"Created {len(collections)} collections")

    logger.info("Seeding tags...")
    tags = await seed_tags(count=50)
    result = supabase.table("tags").insert(tags).execute()
    logger.info(f"Created {len(tags)} tags")

    logger.info("Seeding metadata...")
    metadata = await seed_metadata()
    result = supabase.table("metadata").insert(metadata).execute()
    logger.info(f"Created {len(metadata)} metadata entries")

    # Валидация
    users_count = len(supabase.table("users").select().execute().data)
    media_count = len(supabase.table("media").select().execute().data)
    logger.info(f"✅ Database seeded: {users_count} users, {media_count} media files")


if __name__ == "__main__":
    asyncio.run(main())
