"""Проверка локальной SQLite-базы tg-userbot.

Postgres, Redis и FastAPI в этом проекте нет. Скрипт не создаёт базу
и не ходит в Telegram. Секреты не печатает.

    .\\.venv\\Scripts\\python.exe scripts\\healthcheck.py
    .\\.venv\\Scripts\\python.exe scripts\\healthcheck.py --db D:\\copy.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_COLUMNS = {
    "groups": ("id", "in_broadcast", "skip_reason"),
    "members": ("user_id", "is_bot", "deleted", "access_hash", "dm_block"),
    "accounts": ("name", "user_id", "daily_budget", "broadcast_budget", "flood_until"),
    "blacklist": ("user_id",),
    "campaigns": ("id", "kind"),
    "sent": ("campaign", "target", "status"),
}


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def database_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    import os
    env = os.environ.get("USERBOT_DB")
    if env:
        return Path(env).expanduser().resolve()
    return (ROOT / "userbot.db").resolve()


def check_sqlite(path: Path) -> list[str]:
    """Список проблем. Пустой список — база читается и схема на месте."""
    if not path.exists():
        return [f"нет файла базы: {path}"]
    if not path.is_file():
        return [f"путь базы не файл: {path}"]
    problems: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [f"база не открывается: {exc}"]
    try:
        conn.execute("PRAGMA query_only=ON")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            problems.append(f"integrity_check: {integrity[0] if integrity else 'пусто'}")
        present = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in present:
                problems.append(f"нет таблицы {table}")
                continue
            have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing = [col for col in columns if col not in have]
            if missing:
                problems.append(
                    f"{table}: нет колонок {', '.join(missing)} — scripts/db-migrate.py upgrade"
                )
    except sqlite3.Error as exc:
        problems.append(f"ошибка чтения: {exc}")
    finally:
        conn.close()
    return problems


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Проверка SQLite-базы tg-userbot.")
    parser.add_argument("--db", help="файл базы; по умолчанию USERBOT_DB или userbot.db")
    args = parser.parse_args(argv)
    path = database_path(args.db)
    problems = check_sqlite(path)
    if problems:
        print(f"FAIL {path}", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"OK {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
