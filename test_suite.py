"""
test_suite.py — Logic verification suite for tg-userbot.
Pytest-compatible test suite with SQLite connection management and clean teardown.
"""

import contextlib
import sys
from pathlib import Path

import pytest

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telethon.tl.types import PeerChannel, PeerChat  # noqa: E402

import bot  # noqa: E402
import web as w  # noqa: E402


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
            with contextlib.suppress(Exception):
                c.close()


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
    member_cols = {r["name"] for r in conn.execute("PRAGMA table_info(members)")}
    group_cols = {r["name"] for r in conn.execute("PRAGMA table_info(groups)")}
    account_cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    assert {"deleted", "access_hash", "dm_block"} <= member_cols, member_cols
    assert "skip_reason" in group_cols, group_cols
    assert "broadcast_budget" in account_cols, account_cols


def test_dm_targeting(seeded_db):
    """dm_targets excludes bots, prior successful campaigns, and filters by group."""
    conn = seeded_db
    t = bot.dm_targets(conn)
    assert {r["user_id"] for r in t} == {1, 2, 4}, [r["user_id"] for r in t]  # 3 bot, 5 sent-ok
    tg = bot.dm_targets(conn, group_id=999)
    assert tg == []


class _User:
    def __init__(self, uid, username, is_bot=False, first="", last="",
                 access_hash=None, deleted=False):
        self.id = uid
        self.username = username
        self.bot = is_bot
        self.first_name = first
        self.last_name = last
        self.access_hash = access_hash
        self.deleted = deleted


def test_dm_skips_bot_usernames_keeps_humans_and_blanks(seeded_db):
    """Ник, оканчивающийся на bot, не попадает в DM, даже если is_bot=0.

    Subbotina / Rabota остаются. Пустой профиль без ника и без access_hash
    в очередь не входит: его некому адресовать. С хешем — входит.
    Повторный save_member поднимает is_bot у уже существующей строки.
    """
    conn = seeded_db
    rows = [
        (6, "Dokument2_bot", "Doc", "", 0),
        (8, "Subbotina", "Анна", "Субботина", 0),
        (9, "Rabota", "Иван", "", 0),
        (10, "", "", "", 0),
        (12, "HelperBOT", "X", "", 0),
    ]
    for uid, un, first, last, botf in rows:
        conn.execute(
            "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,source_group) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, un, first, last, botf, bot.now_iso(), 101),
        )
    conn.commit()
    ids = {r["user_id"] for r in bot.dm_targets(conn)}
    assert 6 not in ids and 12 not in ids and 10 not in ids, ids
    assert {1, 2, 4, 8, 9} <= ids, ids
    conn.execute("UPDATE members SET access_hash=? WHERE user_id=10", (4242,))
    conn.commit()
    ids = {r["user_id"] for r in bot.dm_targets(conn)}
    assert 10 in ids, ids
    free = conn.execute(
        f"SELECT COUNT(*) c FROM members m WHERE {bot.DM_ELIGIBLE_WHERE}"
    ).fetchone()["c"]
    assert free == len(bot.dm_targets(conn))

    assert bot.save_member(conn, _User(6, "Dokument2_bot"), 101) == 0
    conn.commit()
    flag = conn.execute("SELECT is_bot FROM members WHERE user_id=6").fetchone()["is_bot"]
    assert flag == 1, flag

    assert bot.save_member(conn, _User(11, "fresh_bot", first="Bot"), 101) == 1
    conn.commit()
    row = conn.execute(
        "SELECT is_bot, first_name FROM members WHERE user_id=11"
    ).fetchone()
    assert row["is_bot"] == 1 and row["first_name"] == "Bot"
    assert 11 not in {r["user_id"] for r in bot.dm_targets(conn)}
    assert bot.username_is_bot("Subbotina") is False
    assert bot.username_is_bot("rabota") is False
    assert bot.username_is_bot("cryptoaml_chek_bot") is True


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
    _ = seeded_db

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


class _Ent:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Rights:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_classify_broadcast_drops_unreachable():
    """Канал-подписчик, выход и запрет писать не являются целью. Админ канала — да."""
    subscriber = _Ent(broadcast=True, creator=False, admin_rights=None)
    ok, why = bot.classify_broadcast(subscriber)
    assert (ok, why) == (False, "channel_subscriber")

    admin = _Ent(broadcast=True, creator=False, admin_rights=_Rights(post_messages=True))
    assert bot.classify_broadcast(admin) == (True, "")
    creator = _Ent(broadcast=True, creator=True)
    assert bot.classify_broadcast(creator)[0] is True

    left = _Ent(broadcast=False, left=True)
    assert bot.classify_broadcast(left) == (False, "left")
    muted = _Ent(broadcast=False, banned_rights=_Rights(send_messages=True))
    assert bot.classify_broadcast(muted) == (False, "muted")
    closed = _Ent(broadcast=False, default_banned_rights=_Rights(send_messages=True))
    assert bot.classify_broadcast(closed) == (False, "no_send")
    open_chat = _Ent(broadcast=False, megagroup=True)
    assert bot.classify_broadcast(open_chat) == (True, "")
    assert bot.classify_broadcast(None) == (False, "not_member")


def test_classify_dm_and_queue(seeded_db):
    """Удалённые, без адреса и с постоянным отказом не возвращаются в очередь."""
    assert bot.classify_dm({"user_id": 1, "username": "Subbotina", "is_bot": 0})[0] is True
    assert bot.classify_dm({"user_id": 2, "username": "", "is_bot": 0}) == (False, "no_address")
    assert bot.classify_dm({"user_id": 3, "username": "", "is_bot": 0, "access_hash": 7})[0] is True
    assert bot.classify_dm({"user_id": 4, "username": "a", "deleted": 1}) == (False, "deleted")
    assert bot.classify_dm({"user_id": 5, "username": "a", "dm_block": "privacy"}) == (False, "privacy")
    assert bot.classify_dm({"user_id": 8, "username": "a"}, self_id=8) == (False, "self")
    assert bot.classify_dm({"user_id": 8, "username": "a"}, blacklisted=True) == (False, "blacklist")

    conn = seeded_db
    conn.execute(
        "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,source_group,deleted) "
        "VALUES(30,'gone','','',0,?,101,1)",
        (bot.now_iso(),),
    )
    conn.execute(
        "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,source_group,dm_block) "
        "VALUES(31,'quiet','','',0,?,101,'privacy')",
        (bot.now_iso(),),
    )
    conn.commit()
    ids = {r["user_id"] for r in bot.dm_targets(conn)}
    assert 30 not in ids and 31 not in ids, ids
    bot.remember_permanent(conn, "dm", 1, "user_blocked")
    conn.commit()
    assert 1 not in {r["user_id"] for r in bot.dm_targets(conn)}


def test_peerflood_is_not_a_target_failure():
    """PeerFlood останавливает аккаунт и не помечает цель постоянным отказом."""
    assert bot.send_error_class("PeerFloodError") == "spam"
    assert bot.target_disposition("PeerFloodError", "dm") == "halt_dm_queue"
    assert bot.target_disposition("PeerFloodError", "broadcast") == "halt_account"
    assert bot.permanent_reason("PeerFloodError", "dm") is None
    assert bot.target_disposition("FloodWaitError", "dm") == "defer_same_account"
    assert bot.target_disposition("UserPrivacyRestrictedError", "dm") == "remember"
    assert bot.permanent_reason("UserPrivacyRestrictedError", "dm") == "privacy"
    assert bot.target_disposition("ChatRestrictedError", "broadcast") == "remember"
    assert bot.target_disposition("SlowModeWaitError", "broadcast") == "skip"
    assert bot.slowmode_exceeds(_Ent(slowmode_seconds=3600), 60) is True
    assert bot.slowmode_exceeds(_Ent(slowmode_seconds=5), None) is False


def test_broadcast_cap_and_group_flags(seeded_db):
    """Дневной потолок групп режет выборку. Писать можно, если может хоть один аккаунт."""
    trimmed, left = bot.trim_to_cap([1, 2, 3, 4], 15, 13)
    assert trimmed == [1, 2] and left == 2
    assert bot.trim_to_cap([1], 0, 99) == ([1], None)

    conn = seeded_db
    conn.execute(
        "INSERT INTO account_groups(account,group_id,can_write,seen_at) VALUES('main',101,0,?)",
        (bot.now_iso(),),
    )
    conn.execute(
        "INSERT INTO account_groups(account,group_id,can_write,seen_at) VALUES('work2',101,1,?)",
        (bot.now_iso(),),
    )
    bot.refresh_group_flags(conn, 101, "channel_subscriber")
    row = conn.execute("SELECT in_broadcast, skip_reason FROM groups WHERE id=101").fetchone()
    assert row["in_broadcast"] == 1 and row["skip_reason"] == "", dict(row)
    conn.execute("UPDATE account_groups SET can_write=0 WHERE group_id=101")
    bot.refresh_group_flags(conn, 101, "channel_subscriber")
    row = conn.execute("SELECT in_broadcast, skip_reason FROM groups WHERE id=101").fetchone()
    assert row["in_broadcast"] == 0 and row["skip_reason"] == "channel_subscriber", dict(row)


def test_save_member_stores_address_and_deleted(seeded_db):
    conn = seeded_db
    assert bot.save_member(conn, _User(40, "", access_hash=99), 101) == 1
    conn.commit()
    row = conn.execute("SELECT access_hash, deleted FROM members WHERE user_id=40").fetchone()
    assert row["access_hash"] == 99 and row["deleted"] == 0
    assert 40 in {r["user_id"] for r in bot.dm_targets(conn)}
    assert bot.save_member(conn, _User(41, "ghost", deleted=True), 101) == 1
    conn.commit()
    assert 41 not in {r["user_id"] for r in bot.dm_targets(conn)}


if __name__ == "__main__":
    code = pytest.main(["-v", __file__])
    if code == 0:
        print("ALL LOGIC TESTS OK")
    sys.exit(int(code))
