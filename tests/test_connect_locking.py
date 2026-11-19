"""Регресс: connect()/get_client() не должны писать схему под чужой транзакцией.

Раньше connect() при каждом вызове гонял migrate(), а migrate() пишет в базу
(INSERT INTO accounts, ALTER, CREATE INDEX). Внутри cmd_conduct get_client()
открывает своё соединение, пока у вызывающего висит незакоммиченная
транзакция, — второе соединение на запись получало «database is locked»
и аккаунт молча выпадал из прогона.
"""

from __future__ import annotations

import pytest

import bot


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    monkeypatch.setattr(bot, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "sessions" / "main.session"))
    monkeypatch.setattr(bot, "SECRETS_PATH", tmp_path / "account-secrets.json")
    conn = bot.connect()
    yield conn
    conn.close()


def test_connection_under_open_transaction_does_not_migrate(db):
    """Под живой записью connect() не должен пытаться писать схему.

    Именно это раньше давало «database is locked» в get_client: вызов
    connect() запускал migrate() с INSERT/ALTER/CREATE INDEX.
    """
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE accounts SET note=note WHERE name='main'")
    try:
        inner = bot.connect(migrate_schema=False)
        try:
            assert inner.execute(
                "SELECT name FROM accounts WHERE name='main'").fetchone() is not None
        finally:
            inner.close()
    finally:
        db.execute("ROLLBACK")


def test_connect_is_idempotent_after_first_migration(tmp_path, monkeypatch):
    """Повторные connect() на ту же базу не должны требовать записи схемы."""
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    first = bot.connect()
    first.execute("BEGIN IMMEDIATE")
    try:
        second = bot.connect(migrate_schema=False)
        assert second.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
        second.close()
    finally:
        first.execute("ROLLBACK")
        first.close()


def test_new_database_gets_its_own_schema(tmp_path, monkeypatch):
    """Свежая база (другой путь) мигрируется, даже если флаг уже ставили."""
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "a.db")
    bot.connect().close()
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "b.db")
    fresh = bot.connect()
    try:
        tables = {r[0] for r in fresh.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        fresh.close()
    assert {"groups", "members", "campaigns", "sent", "accounts"} <= tables
