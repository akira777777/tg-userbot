"""Стадия 2a ADR-0001: tguserbot.db — соединение, схема, миграции, retry.

Пакет проверяется напрямую на временных базах; связка с bot.py (реэкспорт,
позднее связывание DB_PATH/LEGACY_SESSION) покрывается существующими тестами
test_connect_locking / test_db_retry / test_runtime_safety без правок.
"""

from __future__ import annotations

import sqlite3

import pytest

from tguserbot import db


def test_connect_uses_normal_synchronous(tmp_path):
    conn = db.connect(tmp_path / "u.db", main_session="s")
    try:
        # 1 = NORMAL. FULL fsync на каждый commit стопорил бы соседние аккаунты.
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_connect_creates_schema_and_seeds_main(tmp_path):
    path = tmp_path / "u.db"
    conn = db.connect(path, main_session="C:/sessions/main.session")
    assert conn.row_factory is sqlite3.Row
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"groups", "members", "sent", "campaigns", "accounts", "media"} <= tables
    row = conn.execute("SELECT session FROM accounts WHERE name='main'").fetchone()
    assert row["session"] == "C:/sessions/main.session"
    sent_cols = {r["name"] for r in conn.execute("PRAGMA table_info(sent)")}
    camp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(campaigns)")}
    assert "account" in sent_cols and "account" in camp_cols
    conn.close()


def test_connect_once_per_path(tmp_path):
    path = tmp_path / "u.db"
    first = db.connect(path, main_session="s")
    first.close()
    conn = db.connect(path)  # второй вызов: схема уже готова, сид не дублируется
    n = conn.execute("SELECT COUNT(*) AS n FROM accounts WHERE name='main'").fetchone()["n"]
    assert n == 1
    assert path in db._SCHEMA_READY
    conn.close()


def test_migrate_idempotent(tmp_path):
    path = tmp_path / "u.db"
    conn = db.connect(path, main_session="s")
    db.migrate(conn, "s")  # повторный прогон не падает и не дублирует колонки
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sent)")}
    assert {"account", "msg_id", "replied_at", "blocked_at"} <= cols
    conn.close()


def test_db_call_retry_passes_through():
    calls = []

    def ok():
        calls.append(1)
        return "done"

    assert db.db_call_retry(ok, sleep=lambda s: calls.append(s)) == "done"
    assert len(calls) == 1


def test_db_call_retry_retries_on_locked():
    state = {"n": 0}
    delays = []

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return state["n"]

    assert db.db_call_retry(flaky, sleep=delays.append) == 3
    assert len(delays) == 2  # две паузы между тремя попытками


def test_db_call_retry_reraises_other_errors():
    def boom():
        raise sqlite3.OperationalError("no such table: x")

    with pytest.raises(sqlite3.OperationalError):
        db.db_call_retry(boom, sleep=lambda s: None)


def test_db_call_retry_exhausts_attempts():
    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        db.db_call_retry(always_locked, attempts=2, sleep=lambda s: None)
