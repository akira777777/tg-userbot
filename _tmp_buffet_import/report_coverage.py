"""Read-only анализ покрытия и бюджетов: кто в каких группах, где неполный набор.

Ничего не меняет. Запуск: py -3.15 _tmp_buffet_import/report_coverage.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    total_accounts = conn.execute("SELECT COUNT(*) n FROM accounts").fetchone()["n"]
    disabled = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=0 ORDER BY name")]
    groups = {r["id"]: r for r in conn.execute("SELECT * FROM groups")}
    edges: dict[str, set[int]] = {name: set() for name in accounts}
    for row in conn.execute("SELECT account, group_id, can_write FROM account_groups"):
        if row["account"] in edges and row["can_write"]:
            edges[row["account"]].add(row["group_id"])

    print(json.dumps({
        "accounts_total": total_accounts,
        "accounts_enabled": len(accounts),
        "disabled": disabled,
        "groups_in_db": len(groups),
        "groups_in_broadcast": sum(1 for g in groups.values() if g["in_broadcast"]),
    }, ensure_ascii=False))

    target = set(groups)
    per_account = {name: len(edges[name] & target) for name in accounts}
    print("\n== покрытие по аккаунтам (can_write=1) ==")
    for name in sorted(per_account, key=lambda n: -per_account[n]):
        print(f"  {name:<10} {per_account[name]:>4}")

    union = set().union(*edges.values()) if edges else set()
    inter = set.intersection(*edges.values()) if edges else set()
    print(f"\nunion={len(union)} intersection={len(inter)}")
    missing_by_group = []
    for gid in sorted(union | set(groups)):
        who = [name for name in accounts if gid not in edges[name]]
        if who:
            missing_by_group.append({"group_id": gid,
                                     "title": (groups[gid]["title"] if gid in groups else ""),
                                     "missing": who})
    missing_by_group.sort(key=lambda x: len(x["missing"]))
    print(f"групп с неполным набором: {len(missing_by_group)}")
    print("  из них не хватает только 1 аккаунта:",
          sum(1 for m in missing_by_group if len(m["missing"]) == 1))
    print("  не хватает больше 5:", sum(1 for m in missing_by_group if len(m["missing"]) > 5))
    print("\nпримеры (до 15):")
    for item in missing_by_group[:15]:
        print(f"  {item['group_id']:<14} {item['title'][:30].ljust(30)} "
              f"нет {len(item['missing'])}: {', '.join(item['missing'][:6])}")
    conn.close()


if __name__ == "__main__":
    main()
