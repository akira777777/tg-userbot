import sqlite3
from bot import connect

NEEDLES = (
    "radostnyy", "радостн", "cvv", "card shop", "obnal", "обнал",
    "darknet", "tasty", "bingo", "абузер", "checkerok", "satan checker",
    "bumaga", "eternia cc", "visa market", "rolex", "octopus",
    "преисподняя", "rubix", "frank card", "fortuna", "arbitrox",
    "lab no.1488", "hmmml", "банковское дело", "посвідчення водія",
    "ключик", "black0w", "只出顶级", "thugref", "twiboost", "mister_wire",
    "root links", "serggik", "re*und", "road banks", "prozone main",
    "gulliver", "maxa",
)

def is_junk(title, username):
    blob = f"{title or ''} {username or ''}".lower()
    return any(n in blob for n in NEEDLES)

c = connect()
n = 0
for r in c.execute("select id,gtype,title,username,in_broadcast from groups order by title"):
    if is_junk(r["title"], r["username"]):
        n += 1
        print(f"{r['id']}\t{r['gtype']}\tbc={r['in_broadcast']}\t{(r['title'] or '')[:70]}\t@{r['username'] or ''}")
print("COUNT", n)
