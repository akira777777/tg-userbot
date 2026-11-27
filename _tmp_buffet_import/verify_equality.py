"""Сверка равенства аккаунтов: покрытие групп, бюджеты DM и групп.

Read-only. Показывает, где наборы не совпадают и какие аккаунты выбиваются.
Запуск: py -3.15 _tmp_buffet_import/verify_equality.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tguserbot.reports import account_dm_cap  # noqa: E402

# Рабочий набор: группы, куда писать реально можно (в рассылке) и целевые.
BROADCAST_ONLY = True


def main() -> None:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    where = "WHERE in_broadcast=1" if BROADCAST_ONLY else ""
    groups = {r["id"]: r["title"] for r in conn.execute(f"SELECT id, title FROM groups {where}")}  # nosec B608
    edges: dict[str, set[int]] = {name: set() for name in accounts}
    for row in conn.execute(
            "SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])

    caps = {}
    for name in accounts:
        row = conn.execute("SELECT daily_budget, broadcast_budget, flood_until FROM accounts "
                           "WHERE name=?", (name,)).fetchone()
        caps[name] = {
            "daily_budget": row["daily_budget"],
            "broadcast_budget": row["broadcast_budget"],
            "dm_cap": account_dm_cap(conn, name, row["daily_budget"], now=now),
            "flood_until": row["flood_until"],
        }

    covered = {name: len(edges[name] & set(groups)) for name in accounts}
    union = set().union(*edges.values()) if edges else set()
    inter = set.intersection(*edges.values()) if edges else set()
    missing = {}
    for name in accounts:
        missing[name] = sorted(set(groups) - edges[name])

    print(json.dumps({
        "accounts": len(accounts),
        "groups_scope": len(groups),
        "union": len(union),
        "intersection": len(inter),
        "fully_equal": len(inter & set(groups)) == len(groups),
    }, ensure_ascii=False))

    print("\n== покрытие (в рабочем наборе) ==")
    values = sorted(covered.values())
    for name in sorted(accounts, key=lambda n: (-covered[n], n)):
        gaps = len(missing[name])
        print(f"  {name:<10} покрыто {covered[name]:>4}  не хватает {gaps:>4}  "
              f"DM {caps[name]['dm_cap']}  группы {caps[name]['broadcast_budget']}"
              f"{'  FLOOD' if caps[name]['flood_until'] and caps[name]['flood_until'] > now.isoformat(timespec='seconds') else ''}")

    print("\n== разброс ==")
    if values:
        print(f"  min={values[0]} max={values[-1]} разница={values[-1] - values[0]}")
    dm_caps = sorted({v["dm_cap"] for v in caps.values()})
    b_caps = sorted({v["broadcast_budget"] for v in caps.values()})
    print(f"  DM-потолки: {dm_caps}")
    print(f"  потолки групп: {b_caps}")

    # Кто именно кого не покрывает: важно для «каждый имел возможность другого».
    print("\n== персональные пробелы (топ-15 по размеру) ==")
    for name in sorted(accounts, key=lambda n: -len(missing[n]))[:15]:
        if missing[name]:
            print(f"  {name:<10} нет в {len(missing[name]):>3}: "
                  f"{', '.join(str(g) for g in missing[name][:8])}")
    conn.close()


if __name__ == "__main__":
    main()
