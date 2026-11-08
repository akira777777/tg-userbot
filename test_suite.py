"""
test_suite.py — Logic verification suite for tg-userbot.
Pytest-compatible test suite with SQLite connection management and clean teardown.
"""
import os
import sys
from pathlib import Path
import pytest

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot
import web as w
from telethon.tl.types import PeerChannel, PeerChat


@pytest.fixture
def managed_db(tmp_path, monkeypatch):
    """
    Provides an isolated temporary database environment for testing.
    1. Points USERBOT_DB, USERBOT_SESSIONS, and USERBOT_MEDIA to a unique tmp_path.
    2. Overrides bot.DB_PATH, bot.SESSION_DIR, and bot.MEDIA_DIR to prevent module caching bugs.
    3. Intercepts bot.connect() to track and explicitly close all sqlite3.Connection objects,
       eliminating Python 3.14 ResourceWarnings and unlocking files on Windows.
    """
    test_db_path = tmp_path / "test2.db"
    sessions_path = tmp_path / "sessions"
    media_path = tmp_path / "media_cache"
    sessions_path.mkdir(parents=True, exist_ok=True)
    media_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("USERBOT_DB", str(test_db_path))
    monkeypatch.setenv("USERBOT_SESSIONS", str(sessions_path))
    monkeypatch.setenv("USERBOT_MEDIA", str(media_path))

    monkeypatch.setattr(bot, "DB_PATH", test_db_path)
    monkeypatch.setattr(bot, "SESSION_DIR", sessions_path)
    monkeypatch.setattr(bot, "MEDIA_DIR", media_path)

    open_conns = []
    orig_connect = bot.connect

    def tracked_connect(*args, **kwargs):
        conn = orig_connect(*args, **kwargs)
        open_conns.append(conn)
        return conn

    monkeypatch.setattr(bot, "connect", tracked_connect)

    conn = bot.connect()
    try:
        yield conn
    finally:
        for c in open_conns:
            try:
                c.close()
            except Exception:
                pass


@pytest.fixture
def seeded_db(managed_db):
    """
    Seeds the test database with sample groups, members, campaigns, and sent rows.
    """
    conn = managed_db
    conn.execute(
        "INSERT INTO groups(id,title,username,gtype,added_at) VALUES(101,'G1','g1','megagroup',?)",
        (bot.now_iso(),),
    )
    conn.execute(
        "INSERT INTO groups(id,title,username,gtype,added_at) VALUES(102,'G2','','group',?)",
        (bot.now_iso(),),
    )
    for uid, un, botf in [(1, "a", 0), (2, "b", 0), (3, "c", 1), (4, "d", 0), (5, "e", 0)]:
        conn.execute(
            "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,source_group) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, un, un, "", botf, bot.now_iso(), 101),
        )
        conn.execute("INSERT INTO member_sources(user_id,group_id) VALUES(?,101)", (uid,))
    conn.execute("INSERT INTO campaigns(id,kind,text,created_at) VALUES(9,'dm','old',?)", (bot.now_iso(),))
    conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(9,'5','ok',?)", (bot.now_iso(),))
    conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(9,'4','fail:X',?)", (bot.now_iso(),))
    conn.commit()
    return conn


def test_schema(managed_db):
    """Indexes present and journal mode is WAL."""
    conn = managed_db
    names = {r["name"] for r in conn.execute("PRAGMA index_list(member_sources)")}
    assert any(n.startswith("idx_sources") for n in names), names
    jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert jm == "wal", jm


def test_dm_targeting(seeded_db):
    """dm_targets excludes bots, prior successful campaigns, and filters by group."""
    conn = seeded_db
    t = bot.dm_targets(conn)
    assert {r["user_id"] for r in t} == {1, 2, 4}, [r["user_id"] for r in t]  # 3 bot, 5 sent-ok
    tg = bot.dm_targets(conn, group_id=999)
    assert tg == []


def test_blacklist_resolution_and_dm_filtering(seeded_db):
    """Blacklist by nick resolves to real id and removes user from dm_targets."""
    conn = seeded_db

    class A:
        pass

    a = A()
    a.cmd, a.value, a.reason = "add", "@D", "web"
    bot.cmd_blacklist(a)
    bl = [dict(r) for r in conn.execute("SELECT * FROM blacklist")]
    assert len(bl) == 1 and bl[0]["user_id"] == 4 and bl[0]["username"] == "d", bl  # uid 4 was 'd'
    t2 = {r["user_id"] for r in bot.dm_targets(conn)}
    assert t2 == {1, 2}, t2  # 4 now blacklisted


def test_blacklist_unknown_nick(seeded_db):
    """Unknown nick raises SystemExit with expected message."""

    class A:
        pass

    a = A()
    a.cmd, a.value, a.reason = "add", "@ghost", "web"
    with pytest.raises(SystemExit) as exc_info:
        bot.cmd_blacklist(a)
    assert "не найден" in str(exc_info.value), exc_info.value


def test_pick_target():
    """pick_target: entity from dialogs wins; fallback peers by gtype."""

    class Dummy:
        pass

    d = Dummy()
    d.id = 101
    obj = bot.pick_target({101: d}, {"id": 101, "gtype": "megagroup"})
    assert obj is d
    p = bot.pick_target({}, {"id": 101, "gtype": "megagroup"})
    assert isinstance(p, PeerChannel) and p.channel_id == 101, p
    p2 = bot.pick_target({}, {"id": 102, "gtype": "group"})
    assert isinstance(p2, PeerChat) and p2.chat_id == 102, p2


def test_web_args_validation():
    """build_args hardening: validates required keys, action whitelist, text length, and types."""

    def expect_err(payload, needle):
        with pytest.raises(ValueError) as exc_info:
            w.build_args(payload)
        assert needle in str(exc_info.value), (exc_info.value, needle)

    expect_err({"action": "rm -rf"}, "неизвестное действие")
    expect_err({"action": "broadcast", "dry": False, "text": "  "}, "текст или медиа")
    expect_err({"action": "broadcast", "dry": True, "text": ""}, "текст или медиа")
    expect_err({"action": "blacklist_add", "value": "-rf /"}, "только @ник")
    expect_err({"action": "dmsend", "text": "x", "delay_min": "abc"}, "задержки")


def test_web_args_success():
    """build_args returns correct argument vectors and enforces plain string elements."""
    r, slot, _ = w.build_args({"action": "blacklist_add", "value": "@some_nick", "reason": "-x"})
    assert r == ["blacklist", "add", "@some_nick", "--reason", " -x"] and slot == "db", r

    r, slot, _ = w.build_args(
        {
            "action": "dmsend",
            "text": "привет",
            "dry": True,
            "limit": 5,
            "group": "101",
            "repeat_ok": True,
            "delay_min": 1,
            "delay_max": 2,
            "account": "work2",
        }
    )
    assert (
        r[:2] == ["dmsend", "--text"]
        and "--dry-run" in r
        and "--group" in r
        and "--repeat-ok" in r
        and r[-2:] == ["--account", "work2"]
        and slot == "work2"
    ), (r, slot)

    r, slot, _ = w.build_args({"action": "broadcast", "text": "t" * 4000, "dry": False})
    assert len(r[r.index("--text") + 1]) == w.MAX_TEXT
    # JSON body must not be executable as shell: verify argv is a plain list of strings
    assert all(isinstance(x, str) for x in r)


if __name__ == "__main__":
    code = pytest.main(["-v", __file__])
    if code == 0:
        print("ALL LOGIC TESTS OK")
    sys.exit(int(code))
