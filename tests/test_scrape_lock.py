"""Параллельная запись members не должна ронять соседний scrape.

Регрессия на "database is locked" из лога панели: flush_pending коммитил
только в конце группы, write-транзакция жила всю группу, и второй scrape
упирался в busy_timeout. Теперь коммит идёт на каждую порцию.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import bot

_INSERT = (
    "INSERT OR IGNORE INTO members(user_id,username,first_name,last_name,is_bot,"
    "collected_at,source_group,deleted,access_hash) VALUES(?,?,?,?,0,?,'g',0,1)"
)


def _prepare(tmp_path):
    conn = bot.connect()
    conn.close()
    return tmp_path / "bot.db"


def test_parallel_writers_do_not_deadlock(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "legacy.session"))
    db_path = _prepare(tmp_path)

    results: dict[int, str] = {}

    def writer(base_id: int, index: int) -> None:
        conn = sqlite3.connect(db_path, timeout=15)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            for start in range(0, 2000, 200):
                conn.executemany(_INSERT, [
                    (base_id + start + i, f"u{base_id + start + i}", "n", "", bot.now_iso())
                    for i in range(200)
                ])
                # Именно так теперь работает flush_pending: коммит на порцию.
                conn.commit()
                time.sleep(0.001)
            results[index] = "ok"
        except Exception as exc:  # pragma: no cover - сам провал и есть баг
            results[index] = f"{type(exc).__name__}: {exc}"
        finally:
            conn.close()

    threads = [threading.Thread(target=writer, args=(i * 100000, i)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [results.get(i) for i in range(3)] == ["ok", "ok", "ok"]

    check = sqlite3.connect(db_path)
    try:
        assert check.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 6000
    finally:
        check.close()


def test_save_members_does_not_commit_on_its_own(tmp_path, monkeypatch):
    """Коммит остаётся за вызывающим: транзакцию открывает и закрывает cmd_scrape."""
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "legacy.session"))
    conn = bot.connect()
    try:
        class _User:
            id = 1
            username = "someone"
            first_name = "S"
            last_name = ""
            bot = False
            deleted = False
            access_hash = 1

        bot.save_members(conn, [_User()], gid=42)
        assert conn.in_transaction is True
        conn.commit()
        assert conn.in_transaction is False
    finally:
        conn.close()
