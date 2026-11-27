"""Отложенный fanout watch: ждёт истечения всех flood_until в БД, затем POST /api/fanout {action: watch}.

Использование: python scripts/auto_watch_fanout.py
Работает из корня проекта (userbot.db и панель на 127.0.0.1:${WEBBOT_PORT:-8642}).
"""

import http.client
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("USERBOT_DB", ROOT / "userbot.db"))
PANEL_HOST = "127.0.0.1"
PANEL_PORT = int(os.environ.get("WEBBOT_PORT", "8642"))
BUFFER_SECONDS = 180  # +3 минуты после самого позднего cooldown


def latest_flood_until() -> datetime | None:
    """Самая поздняя метка flood_until среди аккаунтов (или None, если штрафов нет)."""
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        rows = [r[0] for r in con.execute("SELECT flood_until FROM accounts")]
    finally:
        con.close()
    stamps = [r for r in rows if r]
    if not stamps:
        return None
    return max(datetime.fromisoformat(s) for s in stamps)


def fanout_watch() -> dict:
    body = json.dumps({"action": "watch"}).encode("utf-8")
    conn = http.client.HTTPConnection(PANEL_HOST, PANEL_PORT, timeout=60)
    try:
        conn.request("POST", "/api/fanout", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}: {raw[:200]}")
        return json.loads(raw)
    finally:
        conn.close()


def main() -> int:
    target = latest_flood_until()
    now = datetime.now(timezone.utc)
    if target is None:
        print(f"[{now.isoformat(timespec='seconds')}] штрафов нет — запуск через 30 с")
        target = now
        wait = 30.0
    else:
        wait = (target - now).total_seconds() + BUFFER_SECONDS
        print(f"[{now.isoformat(timespec='seconds')}] самый поздний cooldown: {target.isoformat()}")
    if wait < 30:
        wait = 30.0
    wake_at = (now + timedelta(seconds=wait)).isoformat(timespec="seconds")
    print(f"жду {int(wait)} с (до {wake_at})", flush=True)
    deadline = time.monotonic() + wait
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        print(f"  осталось {int(left)} с", flush=True)
        time.sleep(min(60, left))
    try:
        res = fanout_watch()
    except Exception as exc:
        print(f"ОШИБКА запуска fanout: {type(exc).__name__}: {exc}", flush=True)
        return 1
    print("РЕЗУЛЬТАТ fanout watch:", flush=True)
    for name, status in sorted(res.get("results", {}).items()):
        print(f"  {name}: {status}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
