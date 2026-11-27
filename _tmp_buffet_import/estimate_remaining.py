"""Оценка остатка работы у чужих join: сколько вступлений они ещё не сделали.

Read-only. Запуск: py -3.15 _tmp_buffet_import/estimate_remaining.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "_tmp_buffet_import" / "join_progress.json"


def snapshot() -> dict:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = {r["id"] for r in conn.execute(
        "SELECT id FROM groups WHERE in_broadcast=1 AND COALESCE(username,'')<>''")}
    edges: dict[str, set[int]] = {n: set() for n in accounts}
    for row in conn.execute("SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])
    conn.close()
    per = {n: len(edges[n] & groups) for n in accounts}
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "target": len(groups), "per_account": per,
            "total_edges": sum(per.values())}


def main() -> None:
    now = snapshot()
    prev = None
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = None
    need = {n: now["target"] - v for n, v in now["per_account"].items()}
    print(json.dumps({"target": now["target"], "total_edges": now["total_edges"],
                      "edges_missing": sum(need.values())}, ensure_ascii=False))
    if prev:
        done = now["total_edges"] - prev["total_edges"]
        elapsed = (datetime.fromisoformat(now["at"])
                   - datetime.fromisoformat(prev["at"])).total_seconds()
        rate = done / elapsed if elapsed else 0
        print(f"прирост с прошлого замера: +{done} рёбер за {elapsed:.0f} c "
              f"({rate * 60:.1f}/мин)")
        if rate > 0:
            print(f"оценка до полного равенства: "
                  f"{sum(need.values()) / rate / 60:.0f} мин")
    print("отстают сильнее всех: " + ", ".join(
        f"{n}={v}" for n, v in sorted(need.items(), key=lambda x: -x[1])[:6]))
    STATE.write_text(json.dumps(now, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
