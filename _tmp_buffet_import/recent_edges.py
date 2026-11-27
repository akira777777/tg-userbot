"""Свежие записи покрытия: какие аккаунты только что вступали и в какие группы.

Read-only. Запуск: py -3.15 _tmp_buffet_import/recent_edges.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT account, group_id, can_write, seen_at FROM account_groups "
        "ORDER BY seen_at DESC LIMIT 12"))
    for row in rows:
        print(f"  {row['seen_at']}  {row['account']:<10} gid={row['group_id']:<14} "
              f"can_write={row['can_write']}")
    total = conn.execute("SELECT COUNT(*) n FROM account_groups").fetchone()["n"]
    print(f"всего рёбер: {total}")
    conn.close()


if __name__ == "__main__":
    main()
