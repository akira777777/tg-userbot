"""Конвейерный догон покрытия: слот освободился — сразу следующий аккаунт.

Батч «все ждут самого медленного» простаивал: 7 аккаунтов уже закончили, а
конвейер ждал восьмой с его FloodWait. Здесь держится ровно --batch живых
процессов join, каждый завершившийся немедленно заменяется следующим.

Щадящий режим: --limit вступлений за один процесс и --daily-cap вступлений
в день на аккаунт (docs/telegram/limits-and-antispam.md §7.2 — свежим 5-10/день).
Посторонние join-процессы пишут логи не сюда — их аккаунты уходят в --defer,
чтобы не начислять вступления поверх чужих.

Запуск: py -3.15 _tmp_buffet_import/sync_pipeline.py --batch 8 --limit 8 \
  --daily-cap 8 --defer work12,work14
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG = ROOT / "_tmp_buffet_import" / "sync_pipeline.log"


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def public_targets() -> list[int]:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT id, username FROM groups WHERE in_broadcast=1"))
    conn.close()
    return [r["id"] for r in rows if str(r["username"] or "").strip()]


def coverage() -> tuple[dict[str, int], int]:
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = {r["id"] for r in conn.execute(
        "SELECT id FROM groups WHERE in_broadcast=1 AND COALESCE(username,'')<>''")}
    edges: dict[str, set[int]] = {n: set() for n in accounts}
    for row in conn.execute("SELECT account, group_id FROM account_groups WHERE can_write=1"):
        if row["account"] in edges:
            edges[row["account"]].add(row["group_id"])
    conn.close()
    return {n: len(edges[n] & groups) for n in accounts}, len(groups)


def joins_today_by_logs() -> dict[str, int]:
    """Вступления каждого аккаунта за сегодня по логам наших join-процессов.

    Считаем строки «+ …» в файлах join_*.log и pipe_*.log, изменённых после
    локальной полуночи. Вступления посторонних процессов здесь не видны,
    поэтому их аккаунты надо исключать через --defer.
    """
    midnight = datetime.combine(date.today(), datetime.min.time()).timestamp()
    counts: dict[str, int] = {}
    for pattern in ("join_*.log", "pipe_*.log"):
        for path in (ROOT / "_tmp_buffet_import").glob(pattern):
            try:
                if path.stat().st_mtime < midnight:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = path.stem.split("_")[1] if "_" in path.stem else ""
            if not name:
                continue
            joins = sum(1 for line in text.splitlines()
                        if line.lstrip().startswith("+ "))
            counts[name] = counts.get(name, 0) + joins
    return counts


def flood_active() -> set[str]:
    """Аккаунты, у которых сейчас активен flood-cooldown (accounts.flood_until)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(f"file:{ROOT / 'userbot.db'}?mode=ro", uri=True)
    names = {row[0] for row in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 AND flood_until > ?", (now,))}
    conn.close()
    return names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--daily-cap", type=int, default=8)
    ap.add_argument("--delay-min", type=float, default=20.0)
    ap.add_argument("--delay-max", type=float, default=45.0)
    ap.add_argument("--max-passes", type=int, default=3)
    ap.add_argument("--pass-gap-sec", type=float, default=300.0)
    ap.add_argument("--defer", default="",
                    help="аккаунты, которые сегодня не трогаем (через запятую)")
    args = ap.parse_args()

    defer = {x.strip() for x in args.defer.split(",") if x.strip()}
    ids = [str(g) for g in public_targets()]
    per, total = coverage()
    log(f"старт: публичных групп {total}, сумма покрытия "
        f"{sum(per.values())}/{total * len(per)}; лимит {args.limit}/процесс, "
        f"дневной потолок {args.daily_cap}/аккаунт; отложено сегодня {len(defer)}")

    for pass_no in range(1, args.max_passes + 1):
        per, total = coverage()
        frozen = flood_active()
        done_today = joins_today_by_logs()
        queue = []
        for name, covered in per.items():
            if covered >= total or name in defer or name in frozen:
                continue
            headroom = args.daily_cap - done_today.get(name, 0)
            if headroom <= 0:
                continue
            queue.append((covered, name, min(args.limit, headroom)))
        if not queue:
            log("очередь пуста: цель достигнута, flood или потолок дня исчерпан")
            break
        queue.sort()
        log(f"проход {pass_no}: {len(queue)} аккаунтов, "
            f"покрытие {queue[0][0]}–{queue[-1][0]} из {total}")

        running: dict[str, tuple[subprocess.Popen, Path]] = {}
        added_pass = 0
        while queue or running:
            while queue and len(running) < args.batch:
                _, name, proc_limit = queue.pop(0)
                stamp = datetime.now().strftime("%H%M%S")
                out_path = ROOT / "_tmp_buffet_import" / f"pipe_{name}_{stamp}.log"
                cmd = [sys.executable, "bot.py", "join", "--accounts", name,
                       "--limit", str(proc_limit),
                       "--delay-min", str(args.delay_min),
                       "--delay-max", str(args.delay_max),
                       "--groups", *ids]
                with out_path.open("w", encoding="utf-8") as handle:
                    running[name] = (
                        subprocess.Popen(cmd, cwd=str(ROOT), stdout=handle,
                                         stderr=subprocess.STDOUT),
                        out_path,
                    )
            # Слот освободился — его сразу занимает следующий аккаунт.
            time.sleep(5)
            for name in [n for n, (p, _) in running.items() if p.poll() is not None]:
                proc, out_path = running.pop(name)
                added = sum(1 for line in out_path.read_text(
                    encoding="utf-8", errors="replace").splitlines()
                    if line.lstrip().startswith("+ "))
                added_pass += added
                log(f"  {name}: +{added} за процесс (код {proc.returncode})")

        per, total = coverage()
        log(f"проход {pass_no} завершён: +{added_pass} рёбер, сумма покрытия "
            f"{sum(per.values())}/{total * len(per)}")
        if added_pass == 0 and not (flood_active() - defer):
            log("прогресса нет и активного flood нет — на сегодня всё")
            break
        if pass_no < args.max_passes:
            pending = [n for n, c in per.items() if c < total and n not in defer]
            if pending:
                log(f"пауза {args.pass_gap_sec:.0f} с перед проходом {pass_no + 1} "
                    f"(остались: {', '.join(pending)})")
                time.sleep(args.pass_gap_sec)

    per, total = coverage()
    log(json.dumps({"per_account": per, "target": total,
                    "at_target": len([n for n in per if per[n] >= total])},
                   ensure_ascii=False))


if __name__ == "__main__":
    main()
