"""Кто сейчас в cooldown и с каким запасом: оценка, когда join сможет продолжить.

Read-only. Запуск: py -3.15 _tmp_buffet_import/flood_status.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT name, flood_until, COALESCE(spam_strikes,0) strikes FROM accounts "
        "WHERE enabled=1 ORDER BY name"))
    active = []
    for row in rows:
        until = row["flood_until"]
        if until and until > now.isoformat(timespec="seconds"):
            active.append((row["name"], until, row["strikes"]))
    print(f"аккаунтов в cooldown: {len(active)} из {len(rows)}")
    for name, until, strikes in active:
        left = int((datetime.fromisoformat(until) - now).total_seconds())
        print(f"  {name:<10} до {until}  ещё {left // 60} мин  strikes={strikes}")
    fresh = conn.execute(
        "SELECT COUNT(*) n FROM account_groups WHERE seen_at >= ?",
        (now.replace(minute=0, second=0, microsecond=0).isoformat(timespec="seconds"),)
    ).fetchone()["n"]
    print(f"рёбер, обновлённых в текущем часе: {fresh}")
    conn.close()


if __name__ == "__main__":
    main()
