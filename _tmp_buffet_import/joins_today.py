"""Сколько вступлений сделал каждый аккаунт за текущие UTC-сутки.

Read-only: seen_at — момент, когда ребро записано/обновлено (вступление или sync).
Запуск: py -3.15 _tmp_buffet_import/joins_today.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# docs/telegram/limits-and-antispam.md §7.2: свежий аккаунт — 5-10/день.
SAFE_FRESH = 10


def main() -> None:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds")
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT account, COUNT(*) n FROM account_groups WHERE seen_at >= ? "
        "GROUP BY account ORDER BY n DESC", (day_start,)))
    print(f"сутки UTC с {day_start}")
    print(f"{'аккаунт':<10} {'рёбер за сутки':>15}  риск")
    over = []
    for row in rows:
        n = row["n"]
        risk = "ВЫШЕ БЕЗОПАСНОГО" if n > SAFE_FRESH else ""
        if n > SAFE_FRESH:
            over.append(row["account"])
        print(f"{row['account']:<10} {n:>15}  {risk}")
    print(f"\nвыше {SAFE_FRESH}/сутки: {len(over)} аккаунтов → {', '.join(over) or '—'}")
    conn.close()


if __name__ == "__main__":
    main()
