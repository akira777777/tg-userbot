"""Стадия 2b ADR-0001: tguserbot.repo — CRUD, участники, отбор очереди DM.

Пакет проверяется напрямую на временных базах (tguserbot.db.connect);
связка с bot.py покрывается существующими тестами (test_bot_queries,
test_dm_queue, test_mailing, test_scrape_lock) через реэкспорт без правок.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tguserbot import repo
from tguserbot.db import connect


def test_record_edge_upsert_and_refresh(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    conn.execute("INSERT INTO groups(id,title) VALUES(1,'g')")
    repo.record_edge(conn, "main", 1, can_write=None)  # не затирает права
    repo.record_edge(conn, "main", 1, can_write=0)
    cw = conn.execute("SELECT can_write FROM account_groups").fetchone()["can_write"]
    assert cw == 0
    repo.record_edge(conn, "main", 1, can_write=1)
    repo.refresh_group_flags(conn, 1, "")
    flags = conn.execute("SELECT in_broadcast, skip_reason FROM groups WHERE id=1").fetchone()
    assert flags["in_broadcast"] == 1 and flags["skip_reason"] == ""
    conn.close()


def test_refresh_group_flags_respects_manual_stoplist(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    conn.execute("INSERT INTO groups(id,title,skip_reason,active,in_broadcast) "
                 "VALUES(1,'g','payment',0,0)")
    repo.record_edge(conn, "main", 1, can_write=1)
    repo.refresh_group_flags(conn, 1, "")
    row = conn.execute("SELECT in_broadcast, skip_reason FROM groups WHERE id=1").fetchone()
    # Ручной стоп-лист сильнее права записи: группа не возвращается в рассылку.
    assert row["in_broadcast"] == 0 and row["skip_reason"] == "payment"
    assert repo.is_manually_blocked(conn, 1) is True
    conn.close()


def test_apply_write_edge_disable_and_reenable(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    conn.execute("INSERT INTO groups(id,title,in_broadcast) VALUES(1,'g',1)")
    repo.apply_write_edge(conn, "main", 1, False, "no_send")
    row = conn.execute("SELECT in_broadcast, skip_reason FROM groups WHERE id=1").fetchone()
    assert row["in_broadcast"] == 0 and row["skip_reason"] == "no_send"
    repo.apply_write_edge(conn, "main", 1, True, "")
    row = conn.execute("SELECT in_broadcast, skip_reason FROM groups WHERE id=1").fetchone()
    assert row["in_broadcast"] == 1 and row["skip_reason"] == ""
    conn.close()


def test_record_reply_by_msg_id_and_block(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    conn.execute("INSERT INTO campaigns(id,kind,text,created_at,account) "
                 "VALUES(1,'dm','t',?,'main')", (now.isoformat(timespec="seconds"),))
    conn.execute("INSERT INTO sent(campaign,target,status,ts,msg_id) VALUES(1,'100','ok',?,777)",
                 (now.isoformat(timespec="seconds"),))
    conn.execute("INSERT INTO members(user_id,username) VALUES(100,'target')")
    conn.commit()
    rid = repo.record_reply(conn, 100, msg_id=777, now=now)
    assert rid is not None
    assert conn.execute("SELECT replied_at FROM sent WHERE rowid=?",
                        (rid,)).fetchone()["replied_at"]
    repo.record_block(conn, 100, now=now + timedelta(hours=1))
    row = conn.execute("SELECT blocked_at FROM sent WHERE rowid=?", (rid,)).fetchone()
    assert row["blocked_at"]
    member = conn.execute("SELECT dm_block FROM members WHERE user_id=100").fetchone()
    assert member["dm_block"] == "user_blocked"
    conn.close()


def test_register_peerflood_counts_strikes(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert repo.register_peerflood(conn, "main", now=now) == 1
    assert repo.register_peerflood(conn, "main", now=now) == 2
    row = conn.execute("SELECT spam_strikes FROM accounts WHERE name='main'").fetchone()
    assert row["spam_strikes"] == 2
    conn.close()


def test_remember_permanent_dm_and_deleted(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    conn.execute("INSERT INTO members(user_id,username) VALUES(5,'u')")
    conn.commit()
    repo.remember_permanent(conn, "dm", 5, "privacy")
    row = conn.execute("SELECT dm_block, deleted FROM members WHERE user_id=5").fetchone()
    assert row["dm_block"] == "privacy" and not row["deleted"]
    repo.remember_permanent(conn, "dm", 5, "deleted")
    row = conn.execute("SELECT dm_block, deleted FROM members WHERE user_id=5").fetchone()
    assert row["deleted"] == 1
    conn.close()


def test_save_members_upsert_semantics(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    first = SimpleNamespace(id=1, username="ivan", first_name="Иван", last_name="",
                            bot=False, deleted=False, access_hash=None)
    assert repo.save_members(conn, [first], 10) == 1
    again = SimpleNamespace(id=1, username="ivan_new", first_name="НовоеИмя", last_name="",
                            bot=True, deleted=True, access_hash=42)
    assert repo.save_members(conn, [again], 10) == 0  # identity не переписывается
    row = conn.execute("SELECT * FROM members WHERE user_id=1").fetchone()
    assert row["username"] == "ivan" and row["first_name"] == "Иван"
    assert row["is_bot"] == 1 and row["deleted"] == 1 and row["access_hash"] == 42
    src = conn.execute("SELECT group_id FROM member_sources WHERE user_id=1").fetchone()
    assert src["group_id"] == 10
    conn.close()


def test_dm_targets_exclusions_and_candidate_parity(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    now = "2026-11-20T12:00:00+00:00"
    people = [
        (1, "normal", "Обычное", None, 0, now, 10, 0, None, ""),
        (2, "spb_bot", "Ботник", None, 0, now, 10, 0, None, ""),
        (3, "subbotina", "Субботина", None, 0, now, 10, 0, None, ""),
        (4, "quiet", "Тихий", None, 0, now, 10, 0, None, ""),
    ]
    conn.executemany(
        "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,"
        "source_group,deleted,access_hash,dm_block) VALUES(?,?,?,?,?,?,?,?,?,?)", people)
    conn.execute("INSERT INTO blacklist(user_id,username,reason,ts) VALUES(4,'quiet','x',?)",
                 (now,))
    conn.commit()
    ids = [r["user_id"] for r in repo.dm_targets(conn)]
    assert 1 in ids and 3 in ids       # обычный и «Субботина» остаются
    assert 2 not in ids                # ник на ...bot отсечён
    assert 4 not in ids                # blacklist исключён
    # Python-половина согласована с SQL-предикатом: кандидат 3 проходит обе проверки.
    cand = repo.candidate_ids(conn).fetchall()
    ok = {r["user_id"] for r in cand if repo._dm_candidate_ok(
        (r["user_id"], r["username"], r["first_name"], r["access_hash"]))}
    assert 3 in ok and 2 not in ok
    conn.close()


def test_dm_eligible_where_matches_constant():
    assert repo.dm_eligible_where(False) == repo.DM_ELIGIBLE_WHERE
    # repeat_ok снимает только дедуп прошлых успешных DM (JOIN sent); NOT IN
    # blacklist/своих аккаунтов остаются в обоих вариантах.
    assert "JOIN sent" in repo.dm_eligible_where(False)
    assert "JOIN sent" not in repo.dm_eligible_where(True)


def test_sync_account_age_overwrite_semantics(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    age = repo.sync_account_age(conn, "main", "2026-01-01", now=now)
    assert age == repo.account_age_days("2026-01-01", now)
    # overwrite=False не затирает существующую дату
    age2 = repo.sync_account_age(conn, "main", "2026-11-19", now=now)
    assert age2 != age and age2 is not None
    stored = conn.execute("SELECT registered_at FROM accounts WHERE name='main'").fetchone()
    assert stored["registered_at"].startswith("2026-01-01")
    conn.close()


def test_register_media_inserts(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    mid = repo.register_media(conn, "main", 5, "C:/media/x.jpg", "photo", "x",
                              "image/jpeg", 10)
    row = conn.execute("SELECT account, msg_id, path FROM media WHERE id=?", (mid,)).fetchone()
    assert row["account"] == "main" and row["msg_id"] == 5 and row["path"] == "C:/media/x.jpg"
    conn.close()
