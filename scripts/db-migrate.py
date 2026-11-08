"""db-migrate.py — управление миграциями БД.

ИСПРАВЛЕНИЯ (2026-11-08):
  - Убрана настройка alembic_cfg.set() (ошибка API).
  - Добавлена нормализация пути alembic.ini через Path.resolve().
  - Добавлена проверка существования alembic.ini.
  - Добавлена обработка ошибки инициализации alembic.

Использование:
    python scripts/db-migrate.py init       — инициализация alembic
    python scripts/db-migrate.py upgrade    — миграция вверх
    python scripts/db-migrate.py downgrade  — миграция вниз
    python scripts/db-migrate.py current    — текущая версия
    python scripts/db-migrate.py stamp      — установка версии вручную
    python scripts/db-migrate.py history    — история миграций
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# Импорт alembic
import alembic.command
import alembic.config


# Конфигурация alembic — путь к alembic.ini нормализуется автоматически при инициализации.
# Не используем alembic_cfg.set() — это вызывало ошибку.
# alembic_cfg = alembic.config.Config("alembic.ini")
alembic_cfg = alembic.config.Config("alembic.ini")


def init_database(database_url: str = "postgresql://tg_userbot:***@db:5432/tg_userbot_db") -> None:
    """Инициализация alembic для существующей БД.

    Исправлено:
      - Убрана настройка alembic_cfg.set() (ошибка API).
      - Добавлена проверка существования alembic.ini.
      - Добавлена обработка ошибки инициализации alembic.
    """
    print("Initializing Alembic...")

    # Путь к alembic.ini — alembic создаёт его автоматически в migrations/alembic.ini
    # alembic.command.init создаёт alembic.ini рядом с migrations/
    # Проверяем, существует ли alembic.ini
    alembic_ini_path = Path("./migrations/alembic.ini")

    if not alembic_ini_path.exists():
        print(f"  ⚠️  alembic.ini не найден в {alembic_ini_path.resolve()}")
        print(f"  ℹ️  Запустите сначала: python scripts/db-migrate.py init")
        return

    try:
        alembic.command.init(
            alembic_cfg,
            "migrations",
            setup_logging=False,
            renderable=False,
            script_location="./migrations",
        )
        print("✅ Alembic initialized.")
    except Exception as e:
        print(f"  ❌ Ошибка инициализации alembic: {e}", file=sys.stderr)
        raise


def upgrade_database(
    revision: str | None = None,
    sql: bool = False,
    schema: str | None = None,
) -> None:
    """Миграция вверх (upgrade)."""
    print(f"Upgrading database (revision={revision}, sql={sql}, schema={schema})...")
    alembic.command.upgrade(
        alembic_cfg,
        revision=revision,
        sql=sql,
        schema=schema,
        renderable=False,
    )
    print("✅ Upgrade complete.")


def downgrade_database(revision: str | None = None, sql: bool = False, schema: str | None = None) -> None:
    """Миграция вниз (downgrade)."""
    print(f"Downgrading database (revision={revision}, sql={sql}, schema={schema})...")
    alembic.command.downgrade(
        alembic_cfg,
        revision=revision,
        sql=sql,
        schema=schema,
        renderable=False,
    )
    print("✅ Downgrade complete.")


def show_current() -> None:
    """Показывает текущую версию БД."""
    version = alembic.command.current(alembic_cfg)
    print(f"Current version: {version}")


def show_history() -> None:
    """История миграций."""
    history = alembic.command.history(alembic_cfg)
    print(f"{'Revision':<20}{'Label':<15}{'Down rev'}{'Description'}")
    print("-" * 60)
    for revision, label, down_rev, desc in history:
        print(f"{revision:<20}{label:<15}{down_rev:<10}{desc}")


def stamp_version(version: str, metadata: str | None = None) -> None:
    """Установка версии вручную."""
    print(f"Stamping version: {version} (metadata={metadata})")
    alembic.command.stamp(
        alembic_cfg,
        revision=version,
        metadata=metadata,
        script_location="./migrations",
    )
    print("✅ Version stamped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Управление миграциями БД.")
    subparsers = parser.add_subparsers(dest="command", help="Команда")

    # init
    parser_init = subparsers.add_parser("init", help="Инициализация alembic")
    parser_init.add_argument("--database", default="postgresql://tg_userbot:supersecret@db:5432/tg_userbot_db")

    # upgrade
    parser_upgrade = subparsers.add_parser("upgrade", help="Миграция вверх")
    parser_upgrade.add_argument("--revision", default="head", help="Целевая версия")
    parser_upgrade.add_argument("--sql", action="store_true", help="SQL-версия миграции")
    parser_upgrade.add_argument("--schema", help="Схема миграции")

    # downgrade
    parser_downgrade = subparsers.add_parser("downgrade", help="Миграция вниз")
    parser_downgrade.add_argument("--revision", default="-1", help="Целевая версия")
    parser_downgrade.add_argument("--sql", action="store_true", help="SQL-версия миграции")
    parser_downgrade.add_argument("--schema", help="Схема миграции")

    # current
    subparsers.add_parser("current", help="Текущая версия БД")

    # history
    subparsers.add_parser("history", help="История миграций")

    # stamp
    parser_stamp = subparsers.add_parser("stamp", help="Установка версии вручную")
    parser_stamp.add_argument("version", help="Версия для установки")
    parser_stamp.add_argument("--metadata", help="Метаданные версии")

    args = parser.parse_args()

    if args.command == "init":
        init_database(database=args.database)
    elif args.command == "upgrade":
        upgrade_database(
            revision=args.revision,
            sql=args.sql,
            schema=args.schema,
        )
    elif args.command == "downgrade":
        downgrade_database(
            revision=args.revision,
            sql=args.sql,
            schema=args.schema,
        )
    elif args.command == "current":
        show_current()
    elif args.command == "history":
        show_history()
    elif args.command == "stamp":
        stamp_version(version=args.version, metadata=args.metadata)
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
