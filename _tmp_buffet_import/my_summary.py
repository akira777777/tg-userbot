import sqlite3
from pathlib import Path

c = sqlite3.connect(Path(__file__).resolve().parents[1] / "userbot.db")
c.row_factory = sqlite3.Row
core = {
    r["id"]
    for r in c.execute(
        "SELECT id FROM groups WHERE in_broadcast=1 AND COALESCE(username,'')<>''"
    )
}
print("public_broadcast_targets", len(core))
print("in_broadcast_all", c.execute(
    "SELECT COUNT(*) n FROM groups WHERE in_broadcast=1").fetchone()["n"])
accs = [r["name"] for r in c.execute(
    "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
print(f"{'account':<10} {'pub':>5} {'bc_w':>5} {'edges':>6} {'write':>6}")
rows = []
for a in accs:
    pub = c.execute(
        "SELECT COUNT(*) n FROM account_groups WHERE account=? AND can_write=1 "
        f"AND group_id IN ({','.join('?' * len(core))})",
        [a, *core],
    ).fetchone()["n"]
    bc = c.execute(
        "SELECT COUNT(*) n FROM account_groups ag JOIN groups g ON g.id=ag.group_id "
        "WHERE ag.account=? AND g.in_broadcast=1 AND ag.can_write=1",
        (a,),
    ).fetchone()["n"]
    edges = c.execute(
        "SELECT COUNT(*) n FROM account_groups WHERE account=?", (a,)
    ).fetchone()["n"]
    write = c.execute(
        "SELECT COUNT(*) n FROM account_groups WHERE account=? AND can_write=1",
        (a,),
    ).fetchone()["n"]
    rows.append((a, pub, bc, edges, write))
    print(f"{a:<10} {pub:>5} {bc:>5} {edges:>6} {write:>6}")
pubs = [r[1] for r in rows]
print(f"public_spread min={min(pubs)} max={max(pubs)} delta={max(pubs)-min(pubs)}")
disabled = [r["name"] for r in c.execute(
    "SELECT name FROM accounts WHERE enabled=0 ORDER BY name")]
print("disabled", disabled)
c.close()
