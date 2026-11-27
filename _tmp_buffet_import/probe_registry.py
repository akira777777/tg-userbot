"""Временный инспектор реестра: авторизован ли каждый аккаунт и совпадает ли id.

Только чтение: ничего не пишет в userbot.db, сессии не изменяет (connect/disconnect).
Запуск: py -3.15 _tmp_buffet_import/probe_registry.py
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


async def probe(name: str, session: str, api_id: int, api_hash: str, expect_id) -> dict:
    out = {"account": name, "session": Path(session).name, "ok": False}
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
        out.update({"ok": True, "user_id": me.id, "username": me.username or "",
                    "name": " ".join(filter(None, [me.first_name, me.last_name]))})
        if expect_id and int(expect_id) != int(me.id):
            out["id_mismatch"] = f"в реестре {expect_id}"
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
    conn = sqlite3.connect(ROOT / "userbot.db")
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT name, session, user_id, enabled FROM accounts ORDER BY name"))
    conn.close()
    ok = bad = 0
    log_path = ROOT / "_tmp_buffet_import" / "registry_probe.log"
    with log_path.open("w", encoding="utf-8") as log:
        for row in rows:
            res = await probe(row["name"], row["session"], api_id, api_hash, row["user_id"])
            res["enabled"] = row["enabled"]
            ok += int(res["ok"])
            bad += int(not res["ok"])
            line = json.dumps(res, ensure_ascii=False)
            log.write(line + "\n")
            log.flush()
            print(line, flush=True)
        summary = json.dumps({"total": len(rows), "alive": ok, "dead": bad},
                             ensure_ascii=False)
        log.write(summary + "\n")
        log.flush()
        print(summary, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
