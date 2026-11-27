"""Проверка выключенных аккаунтов: жива ли сессия и почему выключен.

Только подключение и чтение профиля; userbot.db не меняется.
Запуск: py -3.15 _tmp_buffet_import/probe_disabled.py
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402

from telethon import TelegramClient  # noqa: E402


async def probe(name: str, session: str, api_id: int, api_hash: str) -> dict:
    out = {"account": name}
    path = Path(session)
    if not path.exists():
        out["error"] = "нет файла сессии"
        return out
    client = TelegramClient(str(path), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            out["error"] = "не авторизован"
            return out
        me = await client.get_me()
        out.update({"ok": True, "user_id": me.id,
                    "name": " ".join(filter(None, [me.first_name, me.last_name]))})
    except Exception as exc:  # noqa: BLE001 — диагностика
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return out


async def main() -> None:
    bot.load_env()
    api_id = int(bot.ENV["API_ID"])
    api_hash = bot.ENV["API_HASH"]
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT name, session, flood_until, COALESCE(spam_strikes,0) strikes, "
        "last_peerflood_at FROM accounts WHERE enabled=0 ORDER BY name"))
    conn.close()
    log_path = ROOT / "_tmp_buffet_import" / "disabled_probe.log"
    with log_path.open("w", encoding="utf-8") as log:
        for row in rows:
            res = await probe(row["name"], row["session"], api_id, api_hash)
            res.update({"flood_until": row["flood_until"], "strikes": row["strikes"],
                        "last_peerflood_at": row["last_peerflood_at"]})
            line = json.dumps(res, ensure_ascii=False)
            log.write(line + "\n")
            log.flush()


if __name__ == "__main__":
    asyncio.run(main())
