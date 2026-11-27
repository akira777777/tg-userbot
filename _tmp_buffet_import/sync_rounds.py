"""Догон покрытия раундами: join по целевому набору, пока есть дефицит.

Каждый раунд — отдельный процесс bot.py join (свои слоты, свои сессии).
Перед стартом ждёт, пока не останется чужих процессов join на тех же слотах.
Запуск: py -3.15 _tmp_buffet_import/sync_rounds.py [--max-rounds N] [--limit L]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JOIN_ARGS_GROUP = "--groups"


def target_groups() -> list[int]:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT id, username, invite_link FROM groups WHERE in_broadcast=1"))
    conn.close()
    # Приватную группу без публичного @username API вступить не может.
    return [r["id"] for r in rows if str(r["username"] or "").strip()]


def coverage() -> tuple[dict[str, int], int, int]:
    """(покрытие по аккаунту, размер целевого набора, пересечение)."""
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = {r["id"] for r in conn.execute("SELECT id FROM groups WHERE in_broadcast=1")}
    edges: dict[str, set[int]] = {n: set() for n in accounts}
    for row in conn.execute("SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])
    conn.close()
    per = {n: len(edges[n] & groups) for n in accounts}
    inter = len(set.intersection(*edges.values()) & groups) if edges and groups else 0
    return per, len(groups), inter


def other_join_running() -> bool:
    """Есть ли чужой bot.py join: два процесса на одних слотах ломают сессии."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*bot.py*join*' -and "
        f"$_.ProcessId -ne {os.getpid()} }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=25)
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001 — нет powershell: не блокируем работу
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rounds", type=int, default=6)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--wait-other", type=int, default=1,
                    help="1 — ждать завершения чужого join")
    args = ap.parse_args()

    log_path = ROOT / "_tmp_buffet_import" / "sync_rounds.log"

    def log(msg: str) -> None:
        line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    if args.wait_other:
        waited = 0
        while other_join_running():
            log(f"ждём завершения чужого join (прошло {waited} c)")
            time.sleep(30)
            waited += 30

    for round_no in range(1, args.max_rounds + 1):
        per, total, inter = coverage()
        if inter == total and total:
            log(f"равенство достигнуто: пересечение {inter}/{total}")
            break
        log(f"раунд {round_no}: целевых групп {total}, пересечение {inter}, "
            f"разброс min={min(per.values())} max={max(per.values())}")
        missing_ids = target_groups()
        cmd = [sys.executable, "bot.py", "join", "--limit", str(args.limit),
               "--delay-min", "18", "--delay-max", "40",
               JOIN_ARGS_GROUP, *[str(g) for g in missing_ids]]
        log("запуск: " + " ".join(cmd[:8]) + f" … ({len(missing_ids)} id)")
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        tail = (proc.stdout or "").strip().splitlines()[-12:]
        for line in tail:
            log("  | " + line)
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()[-5:]
            for line in err:
                log("  ! " + line)
            log(f"раунд {round_no} завершился кодом {proc.returncode} — пауза 60 c")
            time.sleep(60)

    per, total, inter = coverage()
    log(json.dumps({"final_per_account": per, "target_groups": total,
                    "intersection": inter}, ensure_ascii=False))


if __name__ == "__main__":
    main()
