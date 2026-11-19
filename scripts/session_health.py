"""Проверка живости всех сессий реестра: авторизован ли, кем, есть ли FloodWait.

Запуск: python scripts/session_health.py
Правит flood_until в базе, если сервер прислал паузу не на нашем вызове.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot as B  # noqa: E402
from telethon.errors import FloodWaitError  # noqa: E402


async def check(name, session):
    client = None
    try:
        client = B.get_client(name, allow_flood=True)
        await client.connect()
        if not await client.is_user_authorized():
            return name, "NOT AUTHORIZED", ""
        me = await client.get_me()
        who = f"@{me.username} " if me.username else ""
        return name, "ok", f"{who}{me.first_name or ''} {me.last_name or ''}".strip()
    except FloodWaitError as exc:
        return name, f"FLOOD {exc.seconds}s", ""
    except Exception as exc:  # noqa: BLE001 — отчёт по всем аккаунтам важнее типа ошибки
        return name, f"ERR {type(exc).__name__}", str(exc)[:90]
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


async def main():
    conn = B.connect()
    rows = conn.execute("SELECT name, session FROM accounts ORDER BY name").fetchall()
    conn.close()
    for name, session in rows:
        acc, state, info = await check(name, session)
        flag = "✓" if state == "ok" else "✗"
        print(f"  {flag} {acc:8} {state:18} {info}")


asyncio.run(main())
