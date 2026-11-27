"""Параллельный догон покрытия: отдельный процесс bot.py join на каждый аккаунт.

Последовательный join тратит паузу 18-40 c на каждое вступление в одном
процессе. Разные аккаунты — разные слоты, поэтому их можно вести одновременно.
Скрипт ждёт, пока не останется чужих join на тех же слотах (иначе SlotBusy).
Запуск: py -3.15 _tmp_buffet_import/sync_parallel.py [--batch 8] [--limit 10]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG = ROOT / "_tmp_buffet_import" / "sync_parallel.log"


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def public_targets() -> list[int]:
    """Группы из рассылки, куда API может вступить: только с @username."""
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT id, username FROM groups WHERE in_broadcast=1"))
    conn.close()
    return [r["id"] for r in rows if str(r["username"] or "").strip()]


def coverage() -> tuple[dict[str, int], int, int]:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = {r["id"] for r in conn.execute(
        "SELECT id FROM groups WHERE in_broadcast=1 AND COALESCE(username, '')<>''")}
    edges: dict[str, set[int]] = {n: set() for n in accounts}
    for row in conn.execute("SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])
    conn.close()
    per = {n: len(edges[n] & groups) for n in accounts}
    inter = len(set.intersection(*edges.values()) & groups) if edges and groups else 0
    return per, len(groups), inter


def foreign_join_running() -> bool:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*bot.py*join*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=25)
        return bool([l for l in out.stdout.splitlines() if l.strip()])
    except Exception:  # noqa: BLE001 — нет powershell: не блокируем
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=8,
                    help="сколько аккаунтов одновременно (по умолчанию 8)")
    ap.add_argument("--limit", type=int, default=10,
                    help="вступлений на аккаунт за запуск")
    ap.add_argument("--delay-min", type=float, default=20.0)
    ap.add_argument("--delay-max", type=float, default=45.0)
    ap.add_argument("--max-rounds", type=int, default=6)
    args = ap.parse_args()

    waited = 0
    while foreign_join_running():
        if waited == 0:
            log("ждём завершения чужих join (они держат слоты аккаунтов)")
        time.sleep(30)
        waited += 30
        if waited % 300 == 0:
            log(f"всё ещё ждём чужих join: {waited} c")
    if waited:
        log(f"чужие join завершились, ждали {waited} c")

    per, total, inter = coverage()
    log(f"старт: целевых публичных групп {total}, пересечение {inter}")
    accounts = sorted(per, key=lambda n: (per[n], n))

    for round_no in range(1, args.max_rounds + 1):
        per, total, inter = coverage()
        todo = [n for n in accounts if per[n] < total]
        if not todo:
            log(f"равенство достигнуто: у всех {total} из {total}")
            break
        log(f"раунд {round_no}: осталось {len(todo)} аккаунтов, "
            f"разброс {min(per.values())}–{max(per.values())}")
        id_args = [str(g) for g in public_targets()]

        for start in range(0, len(todo), args.batch):
            chunk = todo[start:start + args.batch]
            procs = []
            for name in chunk:
                out_path = ROOT / "_tmp_buffet_import" / f"join_{name}_{round_no}.log"
                cmd = [sys.executable, "bot.py", "join", "--accounts", name,
                       "--limit", str(args.limit),
                       "--delay-min", str(args.delay_min),
                       "--delay-max", str(args.delay_max),
                       "--groups", *id_args]
                handle = out_path.open("w", encoding="utf-8")
                procs.append((name, out_path,
                              subprocess.Popen(cmd, cwd=str(ROOT), stdout=handle,
                                               stderr=subprocess.STDOUT)))
            # Батч ждёт целиком: 26 сессий сразу растягивают FloodWait на joinChannel.
            for name, out_path, proc in procs:
                proc.wait()
                lines = out_path.read_text(encoding="utf-8",
                                           errors="replace").strip().splitlines()
                log(f"  {name}: {lines[-1] if lines else '(нет вывода)'}")

    per, total, inter = coverage()
    log(json.dumps({"per_account": per, "target": total, "intersection": inter},
                   ensure_ascii=False))


if __name__ == "__main__":
    main()
