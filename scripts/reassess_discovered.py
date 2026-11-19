"""Пересчитать in_broadcast у групп, найденных discover/seed.

Правило: broadcast-канал (и моноканал без мегагруппы) писать нельзя — только
через админа. Мегагруппа — кандидат: право записи решает groups --probe.
Запуск: python scripts/reassess_discovered.py [--dry-run]
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "userbot.db"
dry = "--dry-run" in sys.argv

conn = sqlite3.connect(DB)
rows = conn.execute(
    "SELECT id, title, gtype, in_broadcast, COALESCE(skip_reason,'') FROM groups "
    "WHERE discovered_at IS NOT NULL").fetchall()
changed = 0
for gid, title, gtype, in_bc, reason in rows:
    readonly = gtype == "broadcast" or reason == "write_closed"
    want = 0 if readonly else 1
    skip = "channel_read_only" if gtype == "broadcast" else (
        "write_closed" if reason == "write_closed" else "")
    if want != in_bc or skip != reason:
        changed += 1
        if dry:
            print(f"[dry] {gid} {title[:40]:40} {gtype:10} {in_bc}->{want} skip={skip or '-'}")
        else:
            conn.execute("UPDATE groups SET in_broadcast=?, skip_reason=? WHERE id=?",
                         (want, skip, gid))
if not dry:
    conn.commit()
print(f"Групп в пересчёте: {changed} из {len(rows)}")
conn.close()
