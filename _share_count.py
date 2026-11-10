import sqlite3

conn = sqlite3.connect("userbot.db")
conn.row_factory = sqlite3.Row
print("groups", conn.execute("select count(*) c from groups").fetchone()["c"])
print(
    "public",
    conn.execute(
        "select count(*) c from groups where coalesce(username,'') != ''"
    ).fetchone()["c"],
)
print(
    "private",
    conn.execute(
        "select count(*) c from groups where coalesce(username,'') = ''"
    ).fetchone()["c"],
)
for name in ("main", "work2"):
    print(
        name,
        "edges",
        conn.execute(
            "select count(*) c from account_groups where account=?", (name,)
        ).fetchone()["c"],
    )

public = conn.execute(
    "select id, username, title from groups where coalesce(username,'') != ''"
).fetchall()
for name in ("main", "work2"):
    have = {
        row["group_id"]
        for row in conn.execute(
            "select group_id from account_groups where account=?", (name,)
        )
    }
    missing = [row for row in public if row["id"] not in have]
    print(name, "missing_public", len(missing))
