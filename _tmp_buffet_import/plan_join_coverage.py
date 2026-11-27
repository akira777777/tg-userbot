"""План вступлений для выравнивания покрытия: кто, куда и можно ли без ссылки.

Read-only. Целевой набор — группы из рассылки (in_broadcast=1). Для каждой
показывает, какие аккаунты вне неё, и отдельно — приватные без invite_link.
Запуск: py -3.15 _tmp_buffet_import/plan_join_coverage.py
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
    groups = [dict(r) for r in conn.execute(
        "SELECT id, title, username, invite_link, gtype FROM groups "
        "WHERE in_broadcast=1 ORDER BY title")]
    edges: dict[str, set[int]] = {name: set() for name in accounts}
    for row in conn.execute("SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])

    target_ids = {g["id"] for g in groups}
    print(json.dumps({
        "accounts": len(accounts),
        "target_groups": len(groups),
        "targets_need_1170_edges": len(accounts) * len(groups),
        "current_edges_inside_target": sum(
            len(edges[n] & target_ids) for n in accounts),
    }, ensure_ascii=False))

    # Группы, куда аккаунт не может вступить через API: приватные без ссылки.
    blocked = [g for g in groups
               if not str(g["username"] or "").strip()
               and not str(g["invite_link"] or "").strip()]
    print(f"\nприватных без invite_link: {len(blocked)}")
    for g in blocked[:20]:
        print(f"  {g['id']:<14} {g['title'][:40]}")

    joinable = [g for g in groups if g not in blocked]
    print(f"доступно для авто-вступления: {len(joinable)}")

    print("\n== дефицит по аккаунтам ==")
    plan_total = 0
    per_account = {}
    for name in accounts:
        need = [g for g in joinable if g["id"] not in edges[name]]
        per_account[name] = need
        plan_total += len(need)
        print(f"  {name:<10} вступить в {len(need):>3} из {len(joinable)}")
    print(f"\nвсего вступлений для полного равенства: {plan_total}")

    # Раунды по лимиту на аккаунт за запуск.
    limit = 6
    rounds = max((len(v) + limit - 1) // limit for v in per_account.values()) if per_account else 0
    print(f"раундов при лимите {limit}/аккаунт: {rounds}")

    out = {name: [g["id"] for g in need] for name, need in per_account.items()}
    (ROOT / "_tmp_buffet_import" / "join_plan.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("план сохранён в _tmp_buffet_import/join_plan.json")
    conn.close()


if __name__ == "__main__":
    main()
