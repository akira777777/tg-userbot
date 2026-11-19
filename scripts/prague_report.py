"""Отчёт по пражскому пулу: какие группы писать, какие каналы — платно."""
import sqlite3
from pathlib import Path

conn = sqlite3.connect(Path(__file__).resolve().parent.parent / "userbot.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT id,title,username,gtype,in_broadcast,COALESCE(skip_reason,'') r "
    "FROM groups ORDER BY title").fetchall()


def bucket(title, gtype, reason):
    t = title.lower()
    if gtype == "broadcast" or reason == "channel_read_only":
        return "каналы (реклама платно)"
    if any(k in t for k in ("работ", "робот", "ваканс", "фушк", "práce", "praca", "job")):
        return "работа"
    if any(k in t for k in ("аренд", "жиль", "сосед", "квартир", "bydlen", "недвиж", "realit")):
        return "жильё/аренда"
    if any(k in t for k in ("барахол", "базар", "bazar", "объявлен", "купи", "продам", "борд")):
        return "барахолка"
    if any(k in t for k in ("студент", "studen", "вопросник", "школ", "kover", "чешск")):
        return "студенты/язык"
    if any(k in t for k in ("афиш", "ивент", "событ", "место", "afish")):
        return "афиша"
    if any(k in t for k in ("новост", "zpráv", "новости")):
        return "новости"
    return "общение/чат"


groups = {}
for row in rows:
    groups.setdefault(bucket(row["title"], row["gtype"], row["r"]), []).append(row)

for name in sorted(groups, key=lambda k: -len(groups[k])):
    items = groups[name]
    wr = sum(1 for i in items if i["in_broadcast"])
    print(f"\n## {name} — {len(items)} (можно писать: {wr})")
    for i in items:
        tag = "✍" if i["in_broadcast"] else "×"
        print(f"  {tag} @{(i['username'] or str(i['id'])):28} {i['title'][:56]}")
