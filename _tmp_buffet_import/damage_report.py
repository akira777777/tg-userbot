"""Отчёт о сегодняшней активности: отправки кампании, их ошибки и новые группы.

Read-only. Запуск: py -3.15 _tmp_buffet_import/damage_report.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAY = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
        microsecond=0).isoformat(timespec="seconds"))


def main() -> None:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) n FROM sent WHERE ts >= ?",
                         (DAY,)).fetchone()["n"]
    kind_status = [dict(r) for r in conn.execute(
        "SELECT status, COUNT(*) n FROM sent WHERE ts >= ? "
        "GROUP BY status ORDER BY n DESC LIMIT 20", (DAY,))]
    per_account_fail = [dict(r) for r in conn.execute(
        "SELECT account, COUNT(*) n FROM sent WHERE ts >= ? AND status <> 'ok' "
        "GROUP BY account ORDER BY n DESC LIMIT 12", (DAY,))]
    new_groups = [dict(r) for r in conn.execute(
        "SELECT id, title, username, in_broadcast, added_at FROM groups "
        "WHERE added_at >= ? ORDER BY added_at", (DAY,))]

    print("== отправки за сегодня ==")
    print("всего записей sent:", total)
    print(json.dumps(kind_status, ensure_ascii=False))
    print("== не-ok по аккаунтам ==")
    print(json.dumps(per_account_fail, ensure_ascii=False))
    print("== группы, добавленные сегодня ==")
    print(json.dumps(new_groups, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
