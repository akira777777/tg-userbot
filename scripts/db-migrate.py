"""Схема SQLite tg-userbot.

Alembic и Postgres здесь нет. Колонки добавляет bot.migrate() при открытии
базы. Откат не поддерживается: старые колонки не удаляются.

    .\\.venv\\Scripts\\python.exe scripts\\db-migrate.py current
    .\\.venv\\Scripts\\python.exe scripts\\db-migrate.py upgrade
    .\\.venv\\Scripts\\python.exe scripts\\db-migrate.py upgrade --db D:\\copy.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HISTORY = (
    "campaigns.account, sent.account",
    "groups.skip_reason",
    "members.deleted, members.access_hash, members.dm_block",
    "accounts.broadcast_budget",
    "сид аккаунта main, если его ещё нет",
)


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def database_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("USERBOT_DB")
    if env:
        return Path(env).expanduser().resolve()
    return (ROOT / "userbot.db").resolve()


def read_tables(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"нет базы: {path}. Создайте её командой upgrade.")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        return [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
            )
        ]
    finally:
        conn.close()


def upgrade(path: Path) -> int:
    os.environ["USERBOT_DB"] = str(path)
    import bot

    bot.DB_PATH = path
    conn = bot.connect()
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
            )
        ]
    finally:
        conn.close()
    print(f"SQLite {path}")
    print("таблицы: " + ", ".join(tables))
    print("схема актуальна (bot.migrate)")
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Схема SQLite tg-userbot (без Alembic).")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="файл базы; по умолчанию USERBOT_DB или userbot.db")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common], help="то же, что upgrade: схема создаётся в bot.py")
    sub.add_parser("upgrade", parents=[common], help="создать базу и добавить недостающие колонки")
    sub.add_parser("current", parents=[common], help="показать таблицы, ничего не записывая")
    sub.add_parser("history", parents=[common], help="какие добавления делает bot.migrate")
    down = sub.add_parser("downgrade", parents=[common], help="не поддерживается")
    down.add_argument("--revision", nargs="?", default="-1")
    stamp = sub.add_parser("stamp", parents=[common], help="не поддерживается")
    stamp.add_argument("version", nargs="?", default="")

    args = parser.parse_args(argv)
    path = database_path(args.db)
    if args.command in ("init", "upgrade"):
        return upgrade(path)
    if args.command == "current":
        tables = read_tables(path)
        print(f"SQLite {path}")
        print("таблицы: " + ", ".join(tables))
        return 0
    if args.command == "history":
        print("Миграции только добавляют колонки, код — bot.migrate():")
        for item in HISTORY:
            print(f"  {item}")
        return 0
    print(
        "Откат не поддерживается: bot.migrate только добавляет колонки и не удаляет данные.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
