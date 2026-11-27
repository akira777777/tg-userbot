"""Разбор целевого набора: какие группы реально достижимы для всех аккаунтов.

Read-only. Делит группы из рассылки на публичные (@username) и приватные,
показывает покрытие по каждой.
Запуск: py -3.15 _tmp_buffet_import/audit_targets.py
"""

from __future__ import annotations

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
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, username, invite_link, gtype, skip_reason FROM groups "
        "WHERE in_broadcast=1 ORDER BY title")]
    edges: dict[int, set[str]] = {}
    for row in conn.execute("SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in accounts:
            edges.setdefault(row["group_id"], set()).add(row["account"])

    public = [g for g in rows if str(g["username"] or "").strip()]
    private_link = [g for g in rows if not str(g["username"] or "").strip()
                    and str(g["invite_link"] or "").strip()]
    private_bare = [g for g in rows if not str(g["username"] or "").strip()
                    and not str(g["invite_link"] or "").strip()]

    print(f"аккаунтов: {len(accounts)}")
    print(f"в рассылке: {len(rows)}")
    print(f"  публичных (@username): {len(public)} — доступны API")
    print(f"  приватных с invite_link: {len(private_link)} — доступны по ссылке")
    print(f"  приватных без ссылки: {len(private_bare)} — НЕДОСТИЖИМЫ автоматически\n")

    def table(title: str, items: list[dict]) -> None:
        if not items:
            return
        print(f"== {title} ==")
        for g in items:
            have = len(edges.get(g["id"], set()))
            mark = "✓" if have == len(accounts) else f"{have}/{len(accounts)}"
            print(f"  {g['id']:<14} {mark:<7} {g['title'][:44]}")
        print()

    table("публичные", public)
    table("приватные с ссылкой", private_link)
    table("приватные без ссылки (только вручную)", private_bare)

    reachable = {g["id"] for g in public + private_link}
    full = sum(1 for g in rows if len(edges.get(g["id"], set())) == len(accounts))
    full_reachable = sum(1 for gid in reachable
                         if len(edges.get(gid, set())) == len(accounts))
    print(f"групп, где есть ВСЕ аккаунты: {full} из {len(rows)}")
    print(f"из достижимых: {full_reachable} из {len(reachable)}")
    conn.close()


if __name__ == "__main__":
    main()
