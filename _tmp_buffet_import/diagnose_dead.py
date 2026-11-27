"""Уточнить причину неавторизованности: бан, отзыв сессии или истёк ключ.

Read-only: подключается, пробует get_me и переводит RPC-ошибку в понятный вывод.
Запуск: py -3.15 _tmp_buffet_import/diagnose_dead.py
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
from telethon.errors import RPCError  # noqa: E402

REASONS = {
    "AUTH_KEY_UNREGISTERED": "сессия отозвана/удалена (нужен вход по номеру)",
    "AUTH_KEY_INVALID": "ключ сессии недействителен (нужен вход по номеру)",
    "SESSION_REVOKED": "сессия завершена из другого клиента",
    "SESSION_EXPIRED": "сессия истекла (нужен вход по номеру)",
    "USER_DEACTIVATED_BAN": "аккаунт забанен Telegram",
    "USER_DEACTIVATED": "аккаунт удалён/деактивирован",
}


async def probe(name: str, session: str, api_id: int, api_hash: str) -> dict:
    out = {"account": name}
    path = Path(session)
    if not path.exists():
        out["verdict"] = "нет файла сессии"
        return out
    client = TelegramClient(str(path), api_id, api_hash)
    try:
        await client.connect()
        try:
            me = await client.get_me()
            out.update({"verdict": "жив", "user_id": getattr(me, "id", None)})
            return out
        except RPCError as exc:
            code = type(exc).__name__
            out.update({"verdict": REASONS.get(code, code), "rpc": code,
                        "detail": str(exc)[:140]})
            return out
    except Exception as exc:  # noqa: BLE001 — диагностика
        out.update({"verdict": f"сбой соединения: {type(exc).__name__}",
                    "detail": str(exc)[:140]})
        return out
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    bot.load_env()
    api_id = int(bot.ENV["API_ID"])
    api_hash = bot.ENV["API_HASH"]
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT name, session FROM accounts WHERE enabled=0 ORDER BY name"))
    conn.close()
    out_path = ROOT / "_tmp_buffet_import" / "dead_diagnosis.json"
    results = []
    for row in rows:
        res = await probe(row["name"], row["session"], api_id, api_hash)
        results.append(res)
        print(json.dumps(res, ensure_ascii=False), flush=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
