"""Временный инспектор: авторизация и Telegram id у скачанных session-файлов.

Не входит в продукт: используется один раз при импорте «бафети»-аккаунтов.
Запуск: py -3.15 _tmp_buffet_import/probe_sessions.py
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402  (нужен ROOT в sys.path)

from telethon import TelegramClient  # noqa: E402


async def probe(path: Path, api_id: int, api_hash: str) -> dict:
    out = {"file": str(path), "name": path.stem, "authorized": False, "error": None}
    client = TelegramClient(str(path), api_id, api_hash)
    try:
        await client.connect()
        out["authorized"] = bool(await client.is_user_authorized())
        if out["authorized"]:
            me = await client.get_me()
            out.update({
                "user_id": me.id,
                "username": me.username or "",
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "phone": me.phone or "",
            })
    except Exception as exc:  # noqa: BLE001 — диагностика: печатаем любой сбой
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return out


async def main() -> None:
    bot.load_env()
    api_id = bot.ENV.get("API_ID")
    api_hash = bot.ENV.get("API_HASH")
    if not api_id or not api_hash:
        print(json.dumps({"fatal": "нет API_ID/API_HASH в .env"}, ensure_ascii=False))
        return
    files = sorted(Path(ROOT / "_tmp_buffet_import").glob("link*/*.session"))
    known = {}
    conn = sqlite3.connect(ROOT / "userbot.db")
    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT name, user_id, session FROM accounts"):
        if row["user_id"]:
            known[int(row["user_id"])] = row["name"]
    conn.close()
    results = []
    for path in files:
        res = await probe(path, int(api_id), api_hash)
        if res.get("user_id"):
            res["already_known_as"] = known.get(int(res["user_id"]))
        results.append(res)
        print(json.dumps(res, ensure_ascii=False))
    ids = [r.get("user_id") for r in results if r.get("user_id")]
    print(json.dumps({
        "total": len(results),
        "authorized": len(ids),
        "unique": len(set(ids)),
        "duplicates_in_batch": sorted({i for i in ids if ids.count(i) > 1}),
        "clash_with_registry": {r["name"]: r["already_known_as"]
                                for r in results if r.get("already_known_as")},
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
