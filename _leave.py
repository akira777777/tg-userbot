import asyncio
from telethon.errors import FloodWaitError, UserNotParticipantError
from telethon.tl.types import Channel, Chat
from bot import classify_broadcast, connect, get_client

NEEDLES = (
    "radostnyy", "радостн", "cvv", "card shop", "obnal", "обнал",
    "darknet", "tasty", "bingo", "абузер", "checkerok", "satan checker",
    "bumaga", "eternia cc", "visa market", "rolex", "octopus",
    "преисподняя", "rubix", "frank card", "fortuna", "arbitrox",
    "lab no.1488", "hmmml", "банковское дело", "посвідчення водія",
    "ключик", "black0w", "只出顶级", "thugref", "twiboost", "mister_wire",
    "root links", "serggik", "re*und", "road banks", "prozone main",
    "gulliver", "maxa", "переходник в резерв", "kotinent",
    "farmacevtika_nemnogo_meta",
)

def is_junk(title, username):
    blob = f"{title or ''} {username or ''}".lower()
    return any(n in blob for n in NEEDLES)

async def leave_one(client, ent):
    try:
        await client.delete_dialog(ent)
        return "left"
    except UserNotParticipantError:
        return "absent"
    except FloodWaitError as err:
        wait = int(getattr(err, "seconds", 5)) + 1
        print(f"  flood {wait}s", flush=True)
        await asyncio.sleep(wait)
        await client.delete_dialog(ent)
        return "left"
    except Exception as err:
        return f"fail {type(err).__name__}"

async def sweep(account, own_ids):
    client = get_client(account)
    await client.connect()
    if not await client.is_user_authorized():
        print(f"auth-fail {account}", flush=True)
        await client.disconnect()
        return []
    gone = []
    try:
        async for dialog in client.iter_dialogs():
            ent = dialog.entity
            if not isinstance(ent, (Channel, Chat)):
                continue
            title = getattr(ent, "title", "") or ""
            username = getattr(ent, "username", "") or ""
            if not is_junk(title, username):
                continue
            can, _why = classify_broadcast(ent)
            if getattr(ent, "broadcast", False) and can:
                own_ids.add(ent.id)
                print(f"keep {account} {ent.id} {title[:60]}", flush=True)
                continue
            status = await leave_one(client, ent)
            if status in ("left", "absent"):
                gone.append(ent.id)
            print(f"{status} {account} {ent.id} {title[:60]}", flush=True)
            await asyncio.sleep(2)
    except Exception as err:
        print(f"sweep-stop {account} {type(err).__name__}: {err}", flush=True)
    await client.disconnect()
    return gone

async def main():
    conn = connect()
    junk_ids = [
        r["id"] for r in conn.execute("SELECT id, title, username, gtype, in_broadcast FROM groups")
        if is_junk(r["title"], r["username"]) and not (r["gtype"] == "channel" and r["in_broadcast"])
    ]
    own_ids = set()
    left = set()
    for account in ("main", "work2"):
        print(f"== {account} ==", flush=True)
        left.update(await sweep(account, own_ids))
    drop = [gid for gid in set(junk_ids).union(left) if gid not in own_ids]
    for gid in drop:
        conn.execute("DELETE FROM account_groups WHERE group_id=?", (gid,))
        conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    conn.commit()
    print(f"DROPPED {len(drop)} OWN_KEPT {len(own_ids)}", flush=True)
    conn.close()

asyncio.run(main())
