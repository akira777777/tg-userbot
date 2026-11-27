"""Кто из аккаунтов уже получал вступления в текущем запуске (по seen_at).

Read-only. Запуск: py -3.15 _tmp_buffet_import/progress_by_account.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = {r["id"] for r in conn.execute(
        "SELECT id FROM groups WHERE in_broadcast=1 AND COALESCE(username,'')<>''")}
    edges: dict[str, set[int]] = {n: set() for n in accounts}
    last: dict[str, str] = {}
    for row in conn.execute(
            "SELECT account, group_id, seen_at FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])
            prev = last.get(row["account"], "")
            if row["seen_at"] and row["seen_at"] > prev:
                last[row["account"]] = row["seen_at"]

    since = (now - timedelta(minutes=25)).isoformat(timespec="seconds")
    active_now = [n for n in accounts if last.get(n, "") >= since]
    idle = [n for n in accounts if last.get(n, "") < since]
    print(f"активных за последние 25 мин: {len(active_now)}")
    print(f"простаивают: {len(idle)} → {', '.join(idle) if idle else '—'}")
    print(f"\n{'аккаунт':<10} {'покрыто':>8} {'осталось':>9}  последнее вступление")
    for name in accounts:
        cov = len(edges[name] & groups)
        mark = "*" if last.get(name, "") >= since else " "
        print(f"{mark}{name:<10} {cov:>8} {len(groups) - cov:>9}  "
              f"{last.get(name, '—')[:19]}")
    conn.close()


if __name__ == "__main__":
    main()
