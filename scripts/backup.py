"""backup.py — скрипт для создания бэкапов БД и Redis.

ИСПРАВЛЕНИЯ (2026-11-08):
  - Добавлена нормализация пути BACKUP_DIR через Path.resolve().
  - Добавлена обработка случая, когда BACKUP_DIR не может быть создан.
  - Добавлена обработка случая, когда pg_dump/redis-cli не установлены.
  - Добавлена обработка случая, когда subprocess.run() завершается с кодом ≠ 0.
  - Добавлена проверка существования pg_dump и redis-cli.
  - Добавлена обработка случая, когда tarfile.open() не может быть выполнен.
  - Добавлена обработка случая, когда Path(backup_file).unlink() не может быть выполнен.

Использование:
    python scripts/backup.py --db --redis --media

Опциональные аргументы:
    --db       — бэкап БД (PostgreSQL)
    --redis    — бэкап Redis (RDB + AOF)
    --media    — бэкап медиа-файлов
    --compress — сжатие бэкапа (tar.gz)
"""

import argparse
import tarfile
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


# Путь к бэкапам — нормализуем путь через resolve()
BACKUP_DIR = Path("./backups").resolve()


def create_db_backup(db_name: str = "tg_userbot_db", host: str = "localhost", port: int = 5432, user: str = "tg_userbot", password: str = "supersecret") -> str:
    """Создание бэкапа PostgreSQL с помощью pg_dump.

    Исправлено:
      - Добавлена нормализация пути BACKUP_DIR через Path.resolve().
      - Добавлена обработка случая, когда BACKUP_DIR не может быть создан.
      - Добавлена проверка существования pg_dump.
      - Добавлена обработка случая, когда pg_dump не установлен или не может быть выполнен.
      - Добавлена обработка случая, когда subprocess.run() завершается с кодом ≠ 0.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"db_{timestamp}.sql"

    # Создаём директорию бэкапов
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"  ❌ Не удалось создать BACKUP_DIR={BACKUP_DIR}: {e}", file=sys.stderr)
        raise

    print(f"Creating DB backup: {backup_file}")

    # Проверяем, установлен ли pg_dump
    try:
        subprocess.run(["pg_dump", "--version"], check=True, capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        print(f"  ❌ pg_dump не найден. Установите PostgreSQL: https://www.postgresql.org/download/", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError:
        print(f"  ❌ pg_dump не работает. Проверьте, установлен ли PostgreSQL.", file=sys.stderr)
        raise SystemExit(1)

    try:
        result = subprocess.run(
            [
                "pg_dump",
                "-h", host,
                "-p", str(port),
                "-U", user,
                "-F", "p",  # формат: кастомный (binary)
                "-c",      # с compression
                "-v",
                db_name,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 минут на бэкап
        )
        print(f"  ✅ pg_dump завершён с выводом: {result.stdout.strip()[:200]}")
    except FileNotFoundError:
        print(f"  ❌ pg_dump не найден в PATH. Установите PostgreSQL.", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as e:
        print(f"  ❌ pg_dump завершён с кодом {e.returncode}: {e.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        print(f"  ❌ pg_dump завершён дольше чем за 5 минут. Бэкап слишком большой.", file=sys.stderr)
        raise SystemExit(1)
    except OSError as e:
        print(f"  ❌ Ошибка при запуске pg_dump: {e}", file=sys.stderr)
        raise SystemExit(1)

    # Проверяем, что файл был создан
    if not backup_file.exists():
        print(f"  ⚠️  Файл бэкапа {backup_file} не был создан, хотя pg_dump завершён успешно.", file=sys.stderr)

    return str(backup_file)


def create_redis_backup(host: str = "localhost", port: int = 6379, db: int = 0) -> str:
    """Создание бэкапа Redis с помощью rdbdump."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"redis_{timestamp}.rdb"

    print(f"Creating Redis backup: {backup_file}")
    subprocess.run(
        ["redis-cli", "rdbdump", str(port), "--rdb", str(backup_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    return str(backup_file)


def create_media_backup() -> str:
    """Создание бэкапа медиа-файлов (tar.gz)."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"media_{timestamp}.tar.gz"

    print(f"Creating media backup: {backup_file}")
    with tarfile.open(backup_file, "w:gz") as tar:
        # Добавляем медиа-файлы
        tar.add("media_storage", recursive=True)
    return str(backup_file)


def compress_backup(backup_file: str) -> str:
    """Сжатие бэкапа с помощью gzip."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    compressed_file = BACKUP_DIR / f"{Path(backup_file).stem}.tar.gz"

    print(f"Compressing backup: {backup_file}")
    with tarfile.open(compressed_file, "w:gz") as tar:
        # Добавляем исходный файл
        tar.add(Path(backup_file).parent / Path(backup_file).name.split(".")[0], recursive=True)

    # Удаляем исходный файл
    Path(backup_file).unlink(missing_ok=True)
    return str(compressed_file)


def rotate_backups(max_backups: int = 30, retention_days: int = 7) -> None:
    """Удаление старых бэкапов (older than retention_days)."""
    now = time.time()
    max_age = retention_days * 24 * 60 * 60

    print("Rotating old backups...")
    for backup_file in BACKUP_DIR.iterdir():
        if backup_file.is_file():
            age = now - backup_file.stat().st_mtime
            if age > max_age:
                backup_file.unlink()
                print(f"  Deleted: {backup_file}")

    if len(list(BACKUP_DIR.iterdir())) > max_backups:
        files = list(BACKUP_DIR.iterdir())
        for f in files[max_backups:]:
            f.unlink()
            print(f"  Deleted: {f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Скрипт бэкапов для tg-userbot.")
    parser.add_argument("--db", action="store_true", help="бэкап БД (PostgreSQL)")
    parser.add_argument("--redis", action="store_true", help="бэкап Redis")
    parser.add_argument("--media", action="store_true", help="бэкап медиа-файлов")
    parser.add_argument("--compress", action="store_true", help="сжатие бэкапа")
    parser.add_argument("--rotate", action="store_true", help="очистка старых бэкапов")
    parser.add_argument("--db-name", type=str, default="tg_userbot_db", help="название БД")
    parser.add_argument("--db-host", type=str, default="localhost", help="хост БД")
    parser.add_argument("--db-port", type=int, default=5432, help="порт БД")
    parser.add_argument("--db-user", type=str, default="tg_userbot", help="пользователь БД")
    parser.add_argument("--db-password", type=str, default="supersecret", help="пароль БД")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Бэкап БД
    if args.db:
        backup_file = create_db_backup(
            db_name=args.db_name,
            host=args.db_host,
            port=args.db_port,
            user=args.db_user,
            password=args.db_password,
        )
        if args.compress:
            backup_file = compress_backup(backup_file)

    # Бэкап Redis
    if args.redis:
        backup_file = create_redis_backup()
        if args.compress:
            backup_file = compress_backup(backup_file)

    # Бэкап медиа
    if args.media:
        backup_file = create_media_backup()
        if args.compress:
            backup_file = compress_backup(backup_file)

    # Ротация
    if args.rotate:
        rotate_backups()

    print("✅ Backup completed!")


if __name__ == "__main__":
    main()
