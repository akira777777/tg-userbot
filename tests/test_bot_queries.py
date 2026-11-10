"""Выбор DM, пакетный scrape и пропуск облака для команд только на чтение."""

from __future__ import annotations

import argparse

import bot


def _connect(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "legacy.session"))
    opened = []
    real_connect = bot.connect

    def tracking_connect():
        conn = real_connect()
        opened.append(conn)
        return conn

    monkeypatch.setattr(bot, "connect", tracking_connect)
    return tracking_connect(), opened


def _index_names(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA index_list({table})")}


class _User:
    def __init__(self, user_id: int, username: str, is_bot: bool = False, *,
                 deleted: bool = False, access_hash: int | None = None,
                 first_name: str | None = None, last_name: str = "") -> None:
        self.id = user_id
        self.username = username
        self.first_name = username if first_name is None else first_name
        self.last_name = last_name
        self.bot = is_bot
        self.deleted = deleted
        self.access_hash = access_hash


def test_hot_indexes_and_dm_targets(tmp_path, monkeypatch):
    conn, opened = _connect(tmp_path, monkeypatch)
    try:
        _assert_indexes_and_targets(conn)
    finally:
        for item in opened:
            item.close()


def _assert_indexes_and_targets(conn):
    assert "idx_sent_ts" in _index_names(conn, "sent")
    assert "idx_sent_account_status_ts" in _index_names(conn, "sent")
    assert "idx_sent_campaign_status_target" in _index_names(conn, "sent")
    assert "idx_members_dm_collected" in _index_names(conn, "members")
    assert "idx_campaigns_kind" in _index_names(conn, "campaigns")
    assert "idx_ag_group" in _index_names(conn, "account_groups")

    conn.execute(
        "INSERT INTO groups(id,title,username,gtype,added_at) VALUES(101,'G1','g1','megagroup',?)",
        (bot.now_iso(),),
    )
    conn.execute(
        "INSERT INTO groups(id,title,username,gtype,added_at) VALUES(102,'G2','','group',?)",
        (bot.now_iso(),),
    )
    for uid, un, botf, gid in [(1, "a", 0, 101), (2, "b", 0, 101), (3, "c", 1, 101), (4, "d", 0, 101), (5, "e", 0, 101), (6, "f", 0, 102)]:
        conn.execute(
            "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,source_group)"
            " VALUES(?,?,?,?,?,?,?)",
            (uid, un, un, "", botf, bot.now_iso(), gid),
        )
        conn.execute("INSERT INTO member_sources(user_id,group_id) VALUES(?,?)", (uid, gid))
    conn.execute("INSERT INTO campaigns(id,kind,text,created_at) VALUES(9,'dm','old',?)", (bot.now_iso(),))
    conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(9,'5','ok',?)", (bot.now_iso(),))
    conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(9,'4','fail:X',?)", (bot.now_iso(),))
    conn.commit()

    assert {row["user_id"] for row in bot.dm_targets(conn)} == {1, 2, 4, 6}
    assert bot.dm_targets(conn, group_id=999) == []
    assert {row["user_id"] for row in bot.dm_targets(conn, group_id=101)} == {1, 2, 4}
    assert {row["user_id"] for row in bot.dm_targets(conn, group_id=102)} == {6}

    args = argparse.Namespace(cmd="add", value="@D", reason="web")
    bot.cmd_blacklist(args)
    assert {row["user_id"] for row in bot.dm_targets(conn)} == {1, 2, 6}

    free = conn.execute(
        "SELECT COUNT(*) c FROM members m WHERE m.is_bot=0 AND m.user_id "
        f"NOT IN (SELECT user_id FROM blacklist) AND {bot.NOT_YET_DMED}"
    ).fetchone()["c"]
    assert free == 3

    # CAST('05') остаётся 5, поэтому очередь та же. Текстовое сравнение этого не делает.
    conn.execute("UPDATE sent SET target='05' WHERE campaign=9 AND target='5'")
    assert {row["user_id"] for row in bot.dm_targets(conn)} == {1, 2, 6}


def test_save_members_commits_once_and_ignores_duplicates(tmp_path, monkeypatch):
    conn, opened = _connect(tmp_path, monkeypatch)
    try:
        _assert_member_batch(conn)
    finally:
        for item in opened:
            item.close()


def _assert_member_batch(conn):
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    users = [_User(1, "a"), _User(2, "b"), _User(3, "c", True)]
    assert bot.save_members(conn, users, 101) == 3
    assert not any(sql.strip().upper() == "COMMIT" for sql in statements)
    conn.commit()
    assert sum(sql.strip().upper() == "COMMIT" for sql in statements) == 1
    assert conn.execute("SELECT COUNT(*) c FROM members").fetchone()["c"] == 3
    assert conn.execute("SELECT COUNT(*) c FROM member_sources").fetchone()["c"] == 3

    assert bot.save_members(conn, users, 101) == 0
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM members").fetchone()["c"] == 3
    assert conn.execute("SELECT COUNT(*) c FROM member_sources").fetchone()["c"] == 3


def test_save_members_promotes_flags_without_rewriting_identity(tmp_path, monkeypatch):
    conn, opened = _connect(tmp_path, monkeypatch)
    try:
        _assert_flag_promotion(conn)
    finally:
        for item in opened:
            item.close()


def _assert_flag_promotion(conn):
    original = _User(1, "kept", first_name="Ann", last_name="Lee")
    assert bot.save_members(conn, [original], 101) == 1
    conn.commit()
    stored = conn.execute("SELECT * FROM members WHERE user_id=1").fetchone()
    collected_at = stored["collected_at"]
    source_group = stored["source_group"]

    updated = _User(
        1, "renamed", is_bot=True, deleted=True, access_hash=123,
        first_name="Other", last_name="Name",
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    assert bot.save_members(conn, [updated], 202) == 0
    assert not any(sql.strip().upper() == "COMMIT" for sql in statements)

    row = conn.execute("SELECT * FROM members WHERE user_id=1").fetchone()
    assert row["username"] == "kept"
    assert row["first_name"] == "Ann"
    assert row["last_name"] == "Lee"
    assert row["collected_at"] == collected_at
    assert row["source_group"] == source_group
    assert row["is_bot"] == 1
    assert row["deleted"] == 1
    assert row["access_hash"] == 123
    sources = {item["group_id"] for item in conn.execute(
        "SELECT group_id FROM member_sources WHERE user_id=1")}
    assert sources == {101, 202}

    cleared = _User(1, "kept")
    assert bot.save_members(conn, [cleared], 101) == 0
    row = conn.execute("SELECT * FROM members WHERE user_id=1").fetchone()
    assert (row["is_bot"], row["deleted"], row["access_hash"]) == (1, 1, 123)


def test_read_only_invocations_skip_cloud_push(monkeypatch):
    monkeypatch.setenv("AUTO_CLOUD", "1")
    started: list = []
    monkeypatch.setattr(bot.subprocess, "Popen", lambda *args, **kwargs: started.append(args))

    bot.maybe_cloud_push(argparse.Namespace(func=bot.cmd_export))
    bot.maybe_cloud_push(argparse.Namespace(func=bot.cmd_blacklist, cmd="list"))
    bot.maybe_cloud_push(argparse.Namespace(func=bot.cmd_accounts, op="list"))
    bot.maybe_cloud_push(argparse.Namespace(func=bot.cmd_groups, list=True, sync=False, probe=False))
    assert started == []

    bot.maybe_cloud_push(argparse.Namespace(func=bot.cmd_blacklist, cmd="add"))
    bot.maybe_cloud_push(argparse.Namespace(func=bot.cmd_groups, list=False, sync=False, probe=True))
    bot.maybe_cloud_push(argparse.Namespace(func=bot.cmd_scrape))
    assert len(started) == 3
