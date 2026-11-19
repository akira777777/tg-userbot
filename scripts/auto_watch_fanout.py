"""Отложенный fanout watch: ждёт истечения всех flood_until в БД, затем POST /api/fanout {action: watch}.

Использование: C:/Users/novra/AppData/Local/Python/pythoncore-3.14-64/python.exe scripts/auto_watch_fanout.py
Работает из корня проекта (userbot.db и панель на 127.0.0.1:8642).
"""

import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "userbot.db"
PANEL = "http://127.0.0.1:8642"
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
    req = urllib.request.Request(
        PANEL + "/api/fanout",
        data=json.dumps({"action": "watch"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    target = latest_flood_until()
    now = datetime.now(timezone.utc)
    if target is None:
        print(f"[{now.isoformat(timespec='seconds')}] штрафов нет — запуск через 30 с")
        target = now
        wait = 30
    else:
        wait = (target - now).total_seconds() + BUFFER_SECONDS
        print(f"[{now.isoformat(timespec='seconds')}] самый поздний cooldown: {target.isoformat()}")
    if wait < 30:
        wait = 30
    print(f"жду {int(wait)} с (до ~{(now.timestamp() + wait) * 1000})", flush=True)
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
