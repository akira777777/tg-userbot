"""Выравнивание бюджетов всех включённых аккаунтов: DM и группы одинаковые.

Ставит daily_budget/broadcast_budget так, чтобы эффективный дневной потолок DM
(с учётом контингента) и потолок групп совпали у всех. Пишет только в userbot.db.
Запуск: py -3.15 _tmp_buffet_import/align_budgets.py [--dry-run]
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tguserbot.budget import COHORT_BUDGET  # noqa: E402
from tguserbot.db import db_call_retry  # noqa: E402
from tguserbot.reports import account_dm_cap  # noqa: E402

# Целевые значения: одинаковые для всех, внутри безопасной эвристики проекта.
TARGET_DM = 20       # потолок для контингента unknown/fresh-с-датой
TARGET_GROUPS = 15

# Явный бюджет должен быть не ниже целевого потолка: cohort_budget() берёт
# min(base, cap), поэтому base >= cap даёт ровно cap.
RAW_DM_BUDGET = 30


def main() -> None:
    dry = "--dry-run" in sys.argv
    conn = sqlite3.connect(ROOT / "userbot.db", timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    now = datetime.now(timezone.utc)
    rows = list(conn.execute(
        "SELECT name, daily_budget, broadcast_budget, cohort, registered_at "
        "FROM accounts WHERE enabled=1 ORDER BY name"))
    print(f"аккаунтов включено: {len(rows)}")
    planned = []
    for row in rows:
        before = account_dm_cap(conn, row["name"], row["daily_budget"], now=now)
        planned.append((row["name"], row["daily_budget"], row["broadcast_budget"],
                        before, row["cohort"]))
    print("\n== до ==")
    for name, dm, bc, cap, cohort in planned:
        print(f"  {name:<10} daily={dm:<3} groups={bc:<3} → DM-потолок {cap}  "
              f"cohort={cohort or 'auto'}")

    if dry:
        print("\n[dry] изменения не записаны")
        conn.close()
        return

    def write() -> None:
        conn.execute(
            "UPDATE accounts SET daily_budget=?, broadcast_budget=? WHERE enabled=1",
            (RAW_DM_BUDGET, TARGET_GROUPS))
        conn.commit()

    db_call_retry(write)

    caps = {name: account_dm_cap(conn, name, RAW_DM_BUDGET, now=now) for name in
            (row["name"] for row in rows)}
    print("\n== после ==")
    for name, cap in sorted(caps.items()):
        budget_row = conn.execute(
            "SELECT daily_budget, broadcast_budget FROM accounts WHERE name=?",
            (name,)).fetchone()
        print(f"  {name:<10} daily={budget_row['daily_budget']:<3} "
              f"groups={budget_row['broadcast_budget']:<3} → DM-потолок {cap}")
    distinct = sorted(set(caps.values()))
    print(f"\nразличных DM-потолков: {len(distinct)} → {distinct}")
    print(f"целевой DM={TARGET_DM} (контингенты: {COHORT_BUDGET})")
    if distinct != [TARGET_DM]:
        print("ВНИМАНИЕ: равенство не достигнуто — проверь cohort/registered_at")
    conn.close()


if __name__ == "__main__":
    main()
