"""Реестр аккаунтов, cooldown и кросс-аккаунтный дедуп — без сети и без боевых файлов."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import bot
import web


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Своя БД и каталог сессий на каждый тест."""
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    monkeypatch.setattr(bot, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "sessions" / "main.session"))
    monkeypatch.setattr(bot, "SECRETS_PATH", tmp_path / "account-secrets.json")
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    monkeypatch.setattr(bot, "LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(bot, "_slot_locker", None)
    bot._covered_slots.clear()
    bot._locked_slots.clear()
    monkeypatch.delenv("USERBOT_SLOT_HELD", raising=False)
    connection = bot.connect()
    try:
        yield connection
    finally:
        bot.release_account_slots()
        connection.close()


def _args(**kwargs) -> argparse.Namespace:
    base = {"op": "list", "name": None, "value": None, "account": "main", "purge": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _dummy_session(tmp_path: Path) -> Path:
    path = tmp_path / "dummy.session"
    path.write_bytes(b"\x00" * 64)
    return path


def _register(conn, name: str, session: str, enabled: int = 1) -> None:
    conn.execute(
        "INSERT INTO accounts(name,session,enabled,added_at) VALUES(?,?,?,?)",
        (name, session, enabled, bot.now_iso()),
    )
    conn.commit()


def test_import_copies_session_into_registry(isolated, tmp_path):
    bot.cmd_accounts(_args(op="import", name="work2", value=str(_dummy_session(tmp_path))))
    row = isolated.execute("SELECT * FROM accounts WHERE name='work2'").fetchone()
    assert row is not None
    dest = Path(row["session"])
    assert dest.name == "work2.session"
    assert dest.parent.name == "sessions"
    assert dest.exists()


def test_import_rejects_session_already_bound(isolated, tmp_path):
    bot.cmd_accounts(_args(op="import", name="work2", value=str(_dummy_session(tmp_path))))
    stored = isolated.execute("SELECT session FROM accounts WHERE name='work2'").fetchone()["session"]
    with pytest.raises(SystemExit, match="уже привязан"):
        bot.cmd_accounts(_args(op="import", name="work3", value=stored))


def test_flood_guard_blocks_account_until_deadline(isolated):
    _register(isolated, "work2", "sessions/work2.session")
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
    isolated.execute("UPDATE accounts SET flood_until=? WHERE name='work2'", (future,))
    isolated.commit()
    with pytest.raises(SystemExit, match="cooldown"):
        bot.flood_guard(isolated, "work2")


def test_disabled_account_does_not_start_client(isolated):
    _register(isolated, "work2", "sessions/work2.session", enabled=0)
    with pytest.raises(SystemExit, match="отключён"):
        bot.get_client("work2")


def test_dm_budget_is_per_account_and_dedups_across_accounts(isolated):
    _register(isolated, "work2", "sessions/work2.session")
    isolated.execute(
        "INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(41,'dm','x',?,'work2')",
        (bot.now_iso(),),
    )
    isolated.execute(
        "INSERT INTO sent(campaign,target,status,ts,account) VALUES(41,'7','ok',?,'work2')",
        (bot.now_iso(),),
    )
    isolated.execute(
        "INSERT INTO members(user_id,username,is_bot,collected_at,source_group) VALUES(7,'g',0,?,101)",
        (bot.now_iso(),),
    )
    isolated.execute("INSERT OR IGNORE INTO member_sources(user_id,group_id) VALUES(7,101)")
    isolated.commit()

    assert bot.today_dm_sent(isolated, "work2") == 1
    assert bot.today_dm_sent(isolated, "main") == 0
    # work2 уже написал пользователю 7 — main не повторяет.
    assert 7 not in {row["user_id"] for row in bot.dm_targets(isolated)}


def test_broadcast_dedup_window_ignores_old_sends(isolated):
    isolated.execute(
        "INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(42,'broadcast','y',?,'work2')",
        (bot.now_iso(),),
    )
    isolated.execute(
        "INSERT INTO sent(campaign,target,status,ts,account) VALUES(42,'555','ok',?,'work2')",
        (bot.now_iso(),),
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
    isolated.execute(
        "INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(43,'broadcast','z',?,'main')",
        (old,),
    )
    isolated.execute(
        "INSERT INTO sent(campaign,target,status,ts,account) VALUES(43,'666','ok',?,?)",
        (old, old),
    )
    isolated.commit()

    recent = bot.recent_ok_targets(isolated, "broadcast", 24)
    assert "555" in recent
    assert "666" not in recent


def test_read_state_counts_dm_per_account(isolated, monkeypatch):
    _register(isolated, "work2", "sessions/work2.session")
    isolated.execute(
        "INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(41,'dm','x',?,'work2')",
        (bot.now_iso(),),
    )
    isolated.execute(
        "INSERT INTO sent(campaign,target,status,ts,account) VALUES(41,'7','ok',?,'work2')",
        (bot.now_iso(),),
    )
    isolated.commit()
    monkeypatch.setattr(web, "DB", bot.DB_PATH)

    state = web.read_state()
    assert "error" not in state, state
    accounts = {item["name"]: item for item in state["accounts"]}
    assert {"main", "work2"} <= set(accounts)
    assert accounts["work2"]["dm_today"] == 1
    assert accounts["work2"]["session"] == "work2.session"
