"""Фикстура в новой SQLite-базе. В боевой userbot.db не пишет.

Раньше скрипт пытался залить вымышленных людей в Supabase. Этот проект
хранит участников в локальном SQLite, и чужое облако ему не нужно.

    .\\.venv\\Scripts\\python.exe scripts\\db-seed.py --db D:\\fixture.db --count 5
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def live_databases() -> set[Path]:
    paths = {(ROOT / "userbot.db").resolve()}
    env = os.environ.get("USERBOT_DB")
    if env:
        paths.add(Path(env).expanduser().resolve())
    return paths


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Создать новую SQLite-фикстуру. Боевую базу не трогает."
    )
    parser.add_argument("--db", required=True, help="новый файл, не userbot.db")
    parser.add_argument("--count", type=int, default=5, help="сколько фиктивных строк, 1..50")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 50:
        print("count: от 1 до 50", file=sys.stderr)
        return 2

    target = Path(args.db).expanduser().resolve()
    if target in live_databases():
        print(f"отказ: db-seed не пишет в боевую базу ({target})", file=sys.stderr)
        return 2
    if target.exists():
        print(f"отказ: файл уже есть, укажите новый путь ({target})", file=sys.stderr)
        return 2
    if target.suffix != ".db":
        print("отказ: путь должен заканчиваться на .db", file=sys.stderr)
        return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    os.environ["USERBOT_DB"] = str(target)
    import bot

    bot.DB_PATH = target
    conn = bot.connect()
    try:
        now = bot.now_iso()
        for i in range(args.count):
            conn.execute(
                "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at) "
                "VALUES(?,?,?,?,0,?)",
                (8_000_000_000_000 + i, f"seed_{i + 1:04d}", "fixture", "", now),
            )
        conn.commit()
        stored = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    finally:
        conn.close()
    print(f"фикстура {target}: строк members {stored}. Это не боевая база.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
