"""Стресс: N процессов пишут в одну базу, как N воркеров watch.

--no-retry пишет напрямую (старое поведение), иначе через persist_group_event.
Выход: количество ошибок 'database is locked', потерянных и записанных групп.
Работает только во временном каталоге — прод-базу не трогает.
Воркер всегда шлёт результат (('ok', ...) или ('err', ...)): смерть воркера
не должна вешать main — сбор результатов идёт с get(timeout=5), а отсутствие
результатов у мёртвых воркеров трактуется как инфраструктурная ошибка (exit 2).
"""
import argparse
import multiprocessing as mp
import queue
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot  # noqa: E402


def _worker(tmp: str, iters: int, use_retry: bool, out: mp.Queue, worker_id: int):
    bot.DB_PATH = Path(tmp) / "stress.db"
    conn = None
    try:
        conn = bot.connect()
        conn.execute("PRAGMA busy_timeout=2000")
        locked = lost = written = 0

        class _Chat:
            def __init__(self, i):
                self.id = 1000000 + worker_id * 10000 + i
                self.title = f"g{worker_id}-{i}"
                self.username = None
                self.broadcast = False
                self.megagroup = True

        for i in range(iters):
            chat = _Chat(i)
            try:
                if use_retry:
                    if bot.persist_group_event(conn, "main", chat) is None:
                        lost += 1
                    else:
                        written += 1
                else:
                    bot.upsert_mailing_group(conn, "main", chat)
                    conn.commit()
                    written += 1
            except sqlite3.OperationalError:
                locked += 1
        out.put(("ok", worker_id, locked, lost, written))
    except Exception as exc:
        out.put(("err", worker_id, repr(exc)))
    finally:
        if conn is not None:
            conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--no-retry", action="store_true")
    args = ap.parse_args()
    t0 = time.monotonic()
    print(f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}")
    print(f"процессов: {args.procs}, режим: "
          f"{'БЕЗ retry (старый)' if args.no_retry else 'с retry (новый)'}")
    with tempfile.TemporaryDirectory() as tmp:
        bot.DB_PATH = Path(tmp) / "stress.db"
        bot.connect().close()  # создать схему до форков
        out = mp.Queue()
        procs = [mp.Process(target=_worker, args=(tmp, args.iters, not args.no_retry, out, i))
                 for i in range(args.procs)]
        for p in procs:
            p.start()
        dead = 0
        res = []
        while len(res) < args.procs:
            try:
                res.append(out.get(timeout=5))
            except queue.Empty:
                if not any(p.is_alive() for p in procs):
                    dead = args.procs - len(res)
                    break
        for p in procs:
            p.join()
    locked = lost = written = 0
    errs = []
    for item in res:
        tag, wid, *rest = item
        if tag == "err":
            errs.append((wid, rest[0]))
        else:
            l, lo, w = rest
            locked += l
            lost += lo
            written += w
    print(f"ошибок 'database is locked': {locked}; потеряно записей: {lost}; записей: {written}")
    for wid, exc in errs:
        print(f"ошибка воркера {wid}: {exc}")
    if dead:
        print(f"ошибка: {dead} воркер(ов) умерли без результата")
    if dead or errs:
        return 2
    if args.no_retry:
        marker = ("REPRODUCED: старое поведение теряет записи" if locked > 0
                  else "не воспроизведено на этой нагрузке")
        code = 0
    else:
        ok = locked == 0 and lost == 0 and written > 0
        marker = "OK: 0 ошибок, 0 потерь" if ok else "FAIL"
        code = 0 if ok else 1
    print(f"elapsed: {time.monotonic() - t0:.1f}s; маркер: {marker}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
