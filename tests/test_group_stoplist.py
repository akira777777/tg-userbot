"""Стоп-лист групп: watch/--sync не воскрешают осознанно выключенные группы.

Иначе дежурный режим возвращал в рассылку чаты, которые уже отклонили
перефорвард или требуют оплаты: он видит их сообщения постоянно.
"""

from __future__ import annotations

import pytest

import bot


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    conn = bot.connect()
    yield conn
    conn.close()


def _group(conn, gid, reason, active, in_broadcast):
    conn.execute(
        "INSERT INTO groups(id,title,username,gtype,added_at,active,in_broadcast,skip_reason) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (gid, f"g{gid}", f"u{gid}", "megagroup", bot.now_iso(), active, in_broadcast, reason))
    bot.record_edge(conn, "acc", gid, can_write=1)
    conn.commit()


def test_manually_blocked_group_stays_out(db):
    _group(db, 1, "forward_blocked", 0, 0)
    bot.refresh_group_flags(db, 1, "")
    row = db.execute("SELECT active, in_broadcast, skip_reason FROM groups WHERE id=1").fetchone()
    assert row["active"] == 0
    assert row["in_broadcast"] == 0
    assert row["skip_reason"] == "forward_blocked"


def test_normal_group_with_write_access_returns(db):
    _group(db, 2, "", 1, 0)
    bot.refresh_group_flags(db, 2, "")
    row = db.execute("SELECT in_broadcast, skip_reason FROM groups WHERE id=2").fetchone()
    assert row["in_broadcast"] == 1
    assert row["skip_reason"] == ""


def test_upsert_does_not_revive_blocked_group(db):
    _group(db, 3, "payment", 0, 0)

    class Ent:
        id = 3
        title = "g3"
        username = "u3"
        broadcast = False
        megagroup = True

    bot.upsert_mailing_group(db, "acc", Ent())
    row = db.execute("SELECT active, in_broadcast, skip_reason FROM groups WHERE id=3").fetchone()
    assert row["in_broadcast"] == 0
    assert row["skip_reason"] == "payment"


def test_is_manually_blocked_covers_inactive(db):
    _group(db, 4, "", 0, 0)
    assert bot.is_manually_blocked(db, 4) is True
