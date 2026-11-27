"""Аудит «бесконечной» рассылки: кто сейчас реально может отправлять.

Read-only. Отвечает на вопросы:
- работает ли conduct, сколько аккаунтов фактически дееспособны;
- какие группы рассылки остались без единого живого отправителя;
- где сегодня ломалось (баны в каналах, unknown_peer,PeerFlood-остатки);
- сколько DM-ёмкости ещё есть (члены вне стоп-листов, остатки бюджета).

Запуск: py -3.15 _tmp_buffet_import/audit_now.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat(timespec="seconds")
DAY = (NOW.replace(hour=0, minute=0, second=0, microsecond=0)
       .isoformat(timespec="seconds"))


def main() -> None:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # --- пул аккаунтов
    acc_rows = list(conn.execute(
        "SELECT name, enabled, COALESCE(spam_strikes,0) strikes, flood_until "
        "FROM accounts ORDER BY enabled DESC, name"))
    enabled = [r for r in acc_rows if r["enabled"]]
    flooded = [r for r in enabled
               if r["flood_until"] and r["flood_until"] > NOW_ISO]
    free = [r["name"] for r in enabled if r not in flooded]
    print(f"аккаунтов всего: {len(acc_rows)}, включено: {len(enabled)}")
    print(f"  в кулдауне (flood): {len(flooded)}")
    for r in flooded[:5]:
        left = int((datetime.fromisoformat(r["flood_until"]) - NOW)
                   .total_seconds() // 60)
        print(f"    {r['name']:<8} ещё {left // 60} ч {left % 60} мин (strikes={r['strikes']})"
              + (f" и ещё {len(flooded) - 5} аккаунтов" if False else ""))
    if len(flooded) > 5:
        print(f"    … и ещё {len(flooded) - 5} с тем же горизонтом")
    print(f"  вне кулдауна: {', '.join(free)}")

    flooded_names = {r["name"] for r in flooded}
    enabled_names = {r["name"] for r in enabled}

    # --- группы рассылки и живые отправители
    groups = list(conn.execute(
        "SELECT id, title, username, skip_reason, active, in_broadcast "
        "FROM groups WHERE in_broadcast=1 ORDER BY title"))
    edges = {}
    for row in conn.execute(
            "SELECT account, group_id FROM account_groups WHERE can_write=1"):
        edges.setdefault(row["group_id"], set()).add(row["account"])
    no_live, dead_skip = [], []
    for g in groups:
        live = {a for a in edges.get(g["id"], set())
                if a in enabled_names and a not in flooded_names}
        if not live:
            no_live.append((g["title"], g["username"] or "-", g["skip_reason"] or ""))
        if g["skip_reason"]:
            dead_skip.append((g["title"], g["skip_reason"]))
    print(f"\nгрупп в рассылке: {len(groups)}")
    print(f"  со skip_reason: {len(dead_skip)}")
    print(f"  без единого живого отправителя сейчас: {len(no_live)}")
    for title, uname, why in no_live[:12]:
        flag = f" [{why}]" if why else ""
        print(f"    «{title}» @{uname}{flag}")
    if len(no_live) > 12:
        print(f"    … и ещё {len(no_live) - 12}")

    # --- отправки сегодня и звук текущей волны
    total = conn.execute("SELECT COUNT(*) n FROM sent WHERE ts >= ?",
                         (DAY,)).fetchone()["n"]
    by_status = [dict(r) for r in conn.execute(
        "SELECT status, COUNT(*) n FROM sent WHERE ts >= ? GROUP BY status "
        "ORDER BY n DESC LIMIT 12", (DAY,))]
    ok_acc = [dict(r) for r in conn.execute(
        "SELECT account, COUNT(*) n FROM sent WHERE ts >= ? AND status='ok' "
        "GROUP BY account ORDER BY n DESC LIMIT 15", (DAY,))]
    print(f"\nотправок за сегодня: {total}")
    print("  по статусам:", json.dumps(by_status, ensure_ascii=False))
    print("  ok по аккаунтам:", json.dumps(ok_acc, ensure_ascii=False))

    banned_targets = [dict(r) for r in conn.execute(
        "SELECT target, COUNT(*) n FROM sent WHERE ts >= ? "
        "AND status LIKE 'fail:UserBanned%' GROUP BY target ORDER BY n DESC LIMIT 15",
        (DAY,))]
    print("  группы-«забанили» (UserBannedInChannel):",
          json.dumps(banned_targets, ensure_ascii=False))

    # --- DM-ёмкость и бюджеты
    members = conn.execute(
        "SELECT COUNT(*) n FROM members WHERE is_bot=0 AND COALESCE(deleted,0)=0 "
        "AND COALESCE(dm_block,'')=''").fetchone()["n"]
    blacklisted = conn.execute("SELECT COUNT(*) n FROM blacklist").fetchone()["n"]
    print(f"\nDM-база: {members} доступных (в blacklist: {blacklisted})")
    # Остаток дневного бюджета: daily 30 минус ok за сегодня
    left_budget = {r["account"]: 30 - r["n"] for r in ok_acc}
    usable = {k: v for k, v in left_budget.items() if k in free}
    print("остаток дневного бюджета у дееспособных:",
          json.dumps(usable, ensure_ascii=False))

    conn.close()


if __name__ == "__main__":
    main()
