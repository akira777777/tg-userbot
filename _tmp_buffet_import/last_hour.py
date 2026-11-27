"""Свежесть потока рассылки и судьба «забанивших» пар.

Read-only. Запуск: py -3.15 _tmp_buffet_import/last_hour.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)
HOUR_AGO = (NOW - timedelta(hours=1)).isoformat(timespec="seconds")


def main() -> None:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    last = conn.execute("SELECT MAX(ts) t FROM sent").fetchone()["t"]
    print("последняя отправка в sent:", last)
    total = conn.execute("SELECT COUNT(*) n FROM sent WHERE ts >= ?",
                         (HOUR_AGO,)).fetchone()["n"]
    print(f"записей sent за последний час: {total}")
    per = [dict(r) for r in conn.execute(
        "SELECT account, status, COUNT(*) n FROM sent WHERE ts >= ? "
        "GROUP BY account, status ORDER BY n DESC LIMIT 15", (HOUR_AGO,))]
    print(json.dumps(per, ensure_ascii=False))

    banned = list(conn.execute(
        "SELECT s.account a, s.target t, ag.can_write cw "
        "FROM sent s LEFT JOIN account_groups ag "
        "ON ag.account = s.account AND ag.group_id = CAST(s.target AS INTEGER) "
        "WHERE s.status LIKE 'fail:UserBanned%' AND s.ts >= "
        "'" + (NOW - timedelta(hours=12)).isoformat(timespec="seconds") + "' "
        "LIMIT 40"))
    marked = sum(1 for r in banned if r["cw"] == 0)
    print(f"пар с UserBannedInChannel за 12 ч: {len(banned)}, "
          f"из них can_write уже = 0: {marked}")
    examples = [(r["a"], r["t"], r["cw"]) for r in banned[:8]]
    print("примеры (аккаунт, группа, can_write):", json.dumps(examples))

    conn.close()


if __name__ == "__main__":
    main()
