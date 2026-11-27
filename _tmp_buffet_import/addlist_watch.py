"""Ждёт освобождения слотов и добавляет чаты папки-инвайта в рассылку.

Сейчас все слоты держит живая кампания `bot.py conduct` — мы её НЕ трогаем.
Наблюдатель раз в минуту пытается запустить join_addlist на зрелых аккаунтах
(список по приоритету). Код 42 = «слот занят» → подождать. Как только
кампания завершится и слот возьмётся — папка «rozyob» будет разобрана
(Check → Join → upsert в группы), с честными edges аккаунта-исполнителя.

Запуск: py -3.15 _tmp_buffet_import/addlist_watch.py   (detached)
Лог: печать → перенаправляется в addlist_watch.log.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve().parent / "join_addlist.py"

# Зрелые аккаунты — по приоритету; свежие (work26-36) сегодня не трогаем.
ORDER = ["dasha", "work3", "work13", "work17", "work18", "work20",
         "work23", "work24", "work25", "work12", "work14", "work15",
         "work19", "work21", "work22"]


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    failures = 0
    # До 4 часов попыток: каждую минуту, по кругу аккаунтов.
    for attempt in range(1, 241):
        for acct in ORDER:
            p = subprocess.run(
                [sys.executable, str(SCRIPT), "--account", acct],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            out = (p.stdout or "") + (p.stderr or "")
            tail = out.strip().splitlines()[-6:]
            log(f"[попытка {attempt}] {acct}: rc={p.returncode} "
                f"{' | '.join(tail[-2:])}")
            if p.returncode == 0:
                log("ГОТОВО: чаты папки добавлены в рассылку")
                log("\n".join(tail))
                return
            if p.returncode == 43:
                # flood-cooldown аккаунта: не ошибка, пробуем следующего.
                continue
            if p.returncode != 42:
                failures += 1
                if failures >= 3:
                    log("стоп: три неудачных попытки подряд")
                    log("\n".join(out.strip().splitlines()[-15:]))
                    return
        time.sleep(60)
    log("таймаут: 4 часа ожидания истекли")


if __name__ == "__main__":
    main()
