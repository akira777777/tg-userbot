import asyncio
from bot import get_client, require_auth

async def main():
    client = get_client("main")
    await require_auth(client)
    n = 0
    async for d in client.iter_dialogs():
        ent = d.entity
        kind = type(ent).__name__
        title = getattr(ent, "title", None) or " ".join(filter(None, [getattr(ent, "first_name", None), getattr(ent, "last_name", None)])) or ""
        username = getattr(ent, "username", "") or ""
        bot = 1 if getattr(ent, "bot", False) else 0
        broadcast = 1 if getattr(ent, "broadcast", False) else 0
        mega = 1 if getattr(ent, "megagroup", False) else 0
        left = 1 if getattr(ent, "left", False) else 0
        print(f"{kind}\tbot={bot}\tbc={broadcast}\tmega={mega}\tleft={left}\tarch={int(bool(d.archived))}\tunread={d.unread_count}\t{title[:70]}\t@{username}")
        n += 1
    print(f"TOTAL {n}")
    await client.disconnect()

asyncio.run(main())
