"""healthcheck.py — скрипт для проверки здоровья процессов.

ИСПРАВЛЕНИЯ (2026-11-08):
  - await conn.close() заменён на обёртку с try/except.
  - redis.Redis() и await r.ping() обернуты в try/except.
  - aiohttp.ClientSession() и session.get() обернуты в try/except.
  - Добавлены импорты: asyncio, asyncpg.exceptions, redis.exceptions, aiohttp.ClientError.

Использует aiohttp для HTTP-запросов и redis-py для проверки Redis.
Вызывается systemd или cron для мониторинга.
"""

import asyncio
import logging
import sys
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


async def check_db(host: str = "localhost", port: int = 5432, user: str = "tg_userbot", password: str = "supersecret", dbname: str = "tg_userbot_db") -> bool:
    """Проверка подключения к PostgreSQL.

    Исправлено:
      - await conn.close() заменён на обёртку с try/except.
      - Добавлена обработка случая, когда asyncpg.connect() не может быть выполнен.
    """
    import asyncpg

    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=dbname,
            command_timeout=2,
        )
        try:
            await conn.close()
        except Exception:
            # conn.close() может выбрасывать ошибку, если соединение уже закрыто
            pass
        return True
    except asyncpg.exceptions.TooManyConnectionsError:
        logger.error(f"DB check failed: слишком много соединений (asyncpg.exceptions.TooManyConnectionsError)")
        return False
    except asyncpg.exceptions.OperationalError as e:
        logger.error(f"DB check failed: ошибка подключения {e}")
        return False
    except asyncpg.exceptions.ConnectionError as e:
        logger.error(f"DB check failed: ошибка соединения {e}")
        return False
    except Exception as e:
        logger.error(f"DB check failed: {e}")
        return False


async def check_redis(host: str = "localhost", port: int = 6379, db: int = 0) -> bool:
    """Проверка подключения к Redis.

    Исправлено:
      - redis.Redis() обёрнут в try/except.
      - await r.ping() обёрнут в try/except.
    """
    import redis

    try:
        r = redis.Redis(host=host, port=port, db=db, decode_responses=True, socket_timeout=2)
    except redis.ConnectionError as e:
        logger.error(f"Redis check failed: ошибка соединения {e}")
        return False
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Redis check failed: ошибка соединения {e}")
        return False
    except Exception as e:
        logger.error(f"Redis check failed: ошибка инициализации {e}")
        return False

    try:
        await r.ping()
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Redis check failed: ping не прошёл {e}")
        return False
    except redis.exceptions.TimeoutError as e:
        logger.error(f"Redis check failed: ping超时 {e}")
        return False
    except Exception as e:
        logger.error(f"Redis check failed: ошибка ping {e}")
        return False

    return True


async def check_app(url: str = "http://localhost:8000") -> bool:
    """Проверка здоровья приложения (FastAPI /health).

    Исправлено:
      - Добавлена обработка случая, когда aiohttp.ClientSession() не может быть создан.
      - Добавлена обработка случая, когда session.get() не может быть выполнен.
      - Добавлена обработка случая, когда response.status != 200.
    """
    try:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=5, connect=3)) as response:
                    if response.status == 200:
                        logger.info("App healthcheck OK (status 200)")
                        return True
                    else:
                        logger.error(f"App healthcheck failed: status {response.status}")
                        return False
            except aiohttp.ClientError as e:
                logger.error(f"App healthcheck failed: aiohttp.ClientError {e}")
                return False
            except asyncio.TimeoutError:
                logger.error("App healthcheck failed: подключение к /health超时ено")
                return False
            except Exception as e:
                logger.error(f"App healthcheck failed: {e}")
                return False
    except Exception as e:
        logger.error(f"App healthcheck failed: {e}")
        return False


async def main() -> int:
    """Главная функция — проверка всех сервисов."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("Starting healthcheck...")

    # Парсим переменные окружения
    import os

    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", 5432))
    db_user = os.getenv("DB_USER", "tg_userbot")
    db_password = os.getenv("DB_PASSWORD", "supersecret")
    db_name = os.getenv("DB_NAME", "tg_userbot_db")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", 6379))
    app_url = os.getenv("APP_URL", "http://localhost:8000")

    # Проверка приложения
    app_ok = await check_app(app_url)
    if not app_ok:
        logger.error("App healthcheck failed")
        return 1

    # Проверка БД
    db_ok = await check_db(host=db_host, port=db_port, user=db_user, password=db_password, dbname=db_name)
    if not db_ok:
        logger.error("DB healthcheck failed")
        return 1

    # Проверка Redis
    redis_ok = await check_redis(host=redis_host, port=redis_port, db=0)
    if not redis_ok:
        logger.error("Redis healthcheck failed")
        return 1

    logger.info("All healthchecks passed!")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
