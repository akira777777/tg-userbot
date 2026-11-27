"""Итоговый отчёт: равенство покрытия, бюджеты, мёртвые аккаунты для входа.

Read-only, печатает markdown-таблицу для отчёта пользователю.
Запуск: py -3.15 _tmp_buffet_import/final_report.py
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tguserbot.reports import account_dm_cap  # noqa: E402


def main() -> None:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")

    all_rows = list(conn.execute(
        "SELECT name, enabled, user_id, daily_budget, broadcast_budget, "
        "registered_at, cohort, flood_until FROM accounts ORDER BY name"))
    enabled = [r["name"] for r in all_rows if r["enabled"]]
    disabled = [r["name"] for r in all_rows if not r["enabled"]]

    groups = {r["id"]: r for r in conn.execute("SELECT * FROM groups")}
    in_bc = {gid for gid, g in groups.items() if g["in_broadcast"]}
    edges: dict[str, set[int]] = {n: set() for n in enabled}
    for row in conn.execute("SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])

    per = {n: len(edges[n] & in_bc) for n in enabled}
    inter = set.intersection(*edges.values()) & in_bc if edges else set()

    print(f"# Отчёт на {stamp}\n")
    print(f"- Всего аккаунтов в реестре: **{len(all_rows)}**")
    print(f"- Включено: **{len(enabled)}**, выключено: **{len(disabled)}**")
    print(f"- Групп в базе: {len(groups)}, из них в рассылке: {len(in_bc)}")
    print(f"- Пересечение покрытия (во всех аккаунтах + в рассылке): **{len(inter)}**")
    equal = len(inter) == len(in_bc) and bool(in_bc)
    print(f"- Полное равенство рабочего набора: **{'ДА' if equal else 'НЕТ'}**\n")

    print("## Покрытие и бюджеты\n")
    print("| аккаунт | покрыто | не хватает | DM-потолок | лимит групп | flood |")
    print("| --- | --- | --- | --- | --- | --- |")
    caps = {}
    for name in sorted(enabled, key=lambda n: (-per[n], n)):
        caps[name] = account_dm_cap(conn, name, None, now=now)
        flood = "да" if any(r["flood_until"] and r["flood_until"] > stamp
                            and r["name"] == name for r in all_rows) else "—"
        print(f"| {name} | {per[name]} | {len(in_bc) - per[name]} | {caps[name]} "
              f"| {next(r['broadcast_budget'] for r in all_rows if r['name'] == name)} | {flood} |")

    spread = max(per.values()) - min(per.values()) if per else 0
    print(f"\nРазброс покрытия: min={min(per.values()) if per else 0}, "
          f"max={max(per.values()) if per else 0}, дельта={spread}")
    print(f"Различных DM-потолков: {sorted(set(caps.values()))}")

    print("\n## Требуют повторного входа\n")
    print("| аккаунт | причина | ID был |")
    print("| --- | --- | --- |")
    for row in all_rows:
        if row["enabled"]:
            continue
        print(f"| {row['name']} | сессия не авторизована | {row['user_id'] or '—'} |")
    conn.close()


if __name__ == "__main__":
    main()
