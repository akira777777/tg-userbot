"""Измерение доставки: ответы, блокировки и честная сводка по кампаниям."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import bot

Q = "'"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    monkeypatch.setattr(bot, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "sessions" / "main.session"))
    monkeypatch.setattr(bot, "SECRETS_PATH", tmp_path / "account-secrets.json")
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    conn = bot.connect()
    try:
        yield conn
    finally:
        conn.close()


def _campaign(conn, kind="dm", text="тест"):
    cur = conn.execute("INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                       (kind, text, bot.now_iso(), "main"))
    conn.commit()
    return cur.lastrowid


def _sent(conn, camp, target, msg_id=None, status="ok", offset_hours=0,
          replied_at=None, blocked_at=None, account="main"):
    ts = (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO sent(campaign,target,status,ts,account,msg_id,replied_at,blocked_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (camp, str(target), status, ts, account, msg_id, replied_at, blocked_at))
    conn.commit()


def test_message_id_reads_simple_message_and_updates():
    class Msg:
        id = 42

    class Upd:
        id = 43

    class Updates:
        updates = [Upd()]

    assert bot.message_id(Msg()) == 42
    assert bot.message_id(Updates()) == 43
    assert bot.message_id(None) is None


def test_new_schema_carries_delivery_columns(db):
    cols = {r["name"] for r in db.execute("PRAGMA table_info(sent)")}
    assert {"msg_id", "replied_at", "blocked_at"} <= cols


def test_reply_by_message_id_marks_that_send(db):
    camp = _campaign(db)
    _sent(db, camp, 1001, msg_id=777)
    _sent(db, camp, 1002, msg_id=778)
    rid = bot.record_reply(db, 1001, msg_id=777, text="да, интересно")
    assert rid is not None
    row = db.execute("SELECT target, replied_at FROM sent WHERE replied_at IS NOT NULL").fetchall()
    assert [r["target"] for r in row] == ["1001"]


def test_reply_without_quote_falls_back_to_recent_dm(db):
    camp = _campaign(db)
    _sent(db, camp, 2001, msg_id=1, offset_hours=2)
    assert bot.record_reply(db, 2001, msg_id=None, text="спасибо") is not None
    assert db.execute("SELECT replied_at FROM sent WHERE target=" + Q + "2001" + Q).fetchone()[0]


def test_reply_older_than_30_days_is_not_attributed(db):
    camp = _campaign(db)
    _sent(db, camp, 3001, msg_id=5, offset_hours=24 * 40)
    assert bot.record_reply(db, 3001) is None


def test_reply_from_unknown_person_is_ignored(db):
    _campaign(db)
    assert bot.record_reply(db, 9999) is None


def test_second_reply_does_not_overwrite_first(db):
    camp = _campaign(db)
    _sent(db, camp, 4001, msg_id=9)
    first = bot.record_reply(db, 4001, msg_id=9)
    second = bot.record_reply(db, 4001, msg_id=9)
    assert first is not None and second is None


def test_block_marks_last_delivery_and_removes_person(db):
    camp = _campaign(db)
    db.execute("INSERT INTO members(user_id,username,first_name,is_bot,collected_at) "
               "VALUES(5001," + Q + "five" + Q + "," + Q + "Five" + Q + ",0,?)", (bot.now_iso(),))
    _sent(db, camp, 5001, msg_id=11)
    bot.record_block(db, 5001)
    assert db.execute("SELECT blocked_at FROM sent WHERE target=" + Q + "5001" + Q).fetchone()[0]
    assert db.execute("SELECT dm_block FROM members WHERE user_id=5001").fetchone()[0] == "user_blocked"


def test_delivery_report_separates_silence_from_evidence(db):
    camp = _campaign(db)
    _sent(db, camp, 1, msg_id=1)
    _sent(db, camp, 2, msg_id=2)
    _sent(db, camp, 3, msg_id=3)
    _sent(db, camp, 4, msg_id=4, status="fail:UserIsBlockedError")
    bot.record_reply(db, 1, msg_id=1)
    bot.record_block(db, 2)
    report = {d["kind"]: d for d in bot.delivery_report(db, days=7)}
    dm = report["dm"]
    assert dm["total"] == 4
    assert dm["accepted"] == 3
    assert dm["replied"] == 1 and dm["blocked"] == 1 and dm["silent"] == 1
    assert dm["failed"] == 1
    assert round(dm["reply_rate"], 1) == 33.3
    assert round(dm["block_rate"], 1) == 33.3


def test_delivery_report_ignores_old_rows(db):
    camp = _campaign(db)
    _sent(db, camp, 1, offset_hours=24 * 30)
    assert bot.delivery_report(db, days=7) == []
