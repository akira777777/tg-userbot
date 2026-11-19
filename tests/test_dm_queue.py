"""Очередь DM: быстрый отбор в Python обязан совпадать с предикатом в SQL.

dm_targets() читает строки по частному индексу и досчитывает условия в Python
(так очередь на круг conduct стоит единицы миллисекунд вместо ~60 мс). Здесь
оба пути прогоняются на одной базе и сверяются до последнего user_id.
"""

from __future__ import annotations

import random

import bot

_GROUPS = 6
_MEMBERS = 400


def _connect(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "legacy.session"))
    return bot.connect()


def _seed(conn):
    now = bot.now_iso()
    for gid in range(1, _GROUPS + 1):
        conn.execute(
            "INSERT INTO groups(id,title,username,gtype,added_at) VALUES(?,?,?,?,?)",
            (1000 + gid, f"G{gid}", f"g{gid}", "megagroup", now),
        )
    # Имена людей с 'bot' внутри (Rabota, BotoxFan, Subbotina) остаются;
    # настоящие боты (оканчиваются на bot) и реклама @SomeBot / t.me/ отсекаются.
    names = ["anna", "petr", "", None, "Rabota", "BotoxFan", "Subbotina", "SomeBot", "helper_bot"]
    firsts = ["Anna", "", None, "  ", "MY AGE APP", "Support Desk", "Casino Boss",
              "Crypto Bro", "@SomeBot", "  x t.me/y  ", "T.ME/promo", "Анна", "support"]
    for uid in range(1, _MEMBERS + 1):
        conn.execute(
            "INSERT INTO members(user_id,username,first_name,last_name,is_bot,"
            "collected_at,source_group,deleted,access_hash,dm_block) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                uid,
                names[uid % len(names)],
                firsts[(uid * 7) % len(firsts)],
                "",
                int(uid % 23 == 0),
                now,
                1000 + (uid % _GROUPS) + 1,
                int(uid % 31 == 0),
                None if uid % 5 == 0 else uid * 3,
                "user_blocked" if uid % 37 == 0 else None,
            ),
        )
        conn.execute("INSERT OR IGNORE INTO member_sources(user_id,group_id) VALUES(?,?)",
                     (uid, 1000 + (uid % _GROUPS) + 1))
    conn.execute("INSERT INTO blacklist(user_id,username,reason,ts) VALUES(?,?,?,?)",
                 (7, "anna", "test", now))
    # main уже создан миграцией; user_id 11 — «свой» аккаунт, он вне очереди.
    conn.execute("UPDATE accounts SET user_id=? WHERE name=?", (11, "main"))
    for cid, kind in ((1, "dm"), (2, "broadcast")):
        conn.execute("INSERT INTO campaigns(kind,text,created_at) VALUES(?,?,?)",
                     (kind, "", now))
    for target, status in (("5", "ok"), ("13", "ok"), ("17", "fail:X")):
        conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(?,?,?,?)",
                     (1, target, status, now))
    conn.commit()


def test_dm_queue_matches_sql_predicate(tmp_path, monkeypatch):
    conn = _connect(tmp_path, monkeypatch)
    try:
        _seed(conn)
        expected = {row["user_id"] for row in conn.execute(
            "SELECT m.user_id FROM members m WHERE " + bot.dm_eligible_where(False))}
        expected_repeat = {row["user_id"] for row in conn.execute(
            "SELECT m.user_id FROM members m WHERE " + bot.dm_eligible_where(True))}

        got = bot.dm_targets(conn)
        missing = sorted(expected - {row["user_id"] for row in got})
        extra = sorted({row["user_id"] for row in got} - expected)
        details = []
        for uid in (missing + extra)[:5]:
            details.append((uid, tuple(conn.execute(
                "SELECT username, first_name, access_hash FROM members WHERE user_id=?",
                (uid,)).fetchone()), bot._dm_candidate_ok(conn.execute(
                "SELECT user_id, username, first_name, access_hash FROM members "
                "WHERE user_id=?", (uid,)).fetchone())))
        assert not missing and not extra, (missing[:5], extra[:5], details)
        assert [row["collected_at"] for row in got] == sorted(
            row["collected_at"] for row in got)
        assert [row["user_id"] for row in got] == sorted(
            row["user_id"] for row in got), "порядок сбора должен сохраняться"
        assert {row["user_id"] for row in bot.dm_targets(conn, repeat_ok=True)} \
            == expected_repeat
        assert {row["user_id"] for row in bot.dm_targets(conn, shuffle=True)} == expected

        # LIMIT отдаёт ровно первые N очереди, а не любые N подряд.
        full = [row["user_id"] for row in bot.dm_targets(conn)]
        assert [row["user_id"] for row in bot.dm_targets(conn, limit=25)] == full[:25]

        group = conn.execute("SELECT group_id FROM member_sources LIMIT 1").fetchone()[0]
        expected_group = {row["user_id"] for row in conn.execute(
            "SELECT m.user_id FROM members m WHERE " + bot.dm_eligible_where(False) +
            " AND m.user_id IN (SELECT user_id FROM member_sources WHERE group_id=?)",
            (group,))}
        assert {row["user_id"] for row in bot.dm_targets(conn, group_id=group)} == expected_group
        assert bot.dm_targets(conn, group_id=-1) == []
        random.shuffle(got)
    finally:
        conn.close()


def test_dm_queue_keys_and_empty_db(tmp_path, monkeypatch):
    conn = _connect(tmp_path, monkeypatch)
    try:
        _seed(conn)
        row = bot.dm_targets(conn, limit=1)[0]
        assert set(row.keys()) == set(bot._MEMBER_COLUMNS)
        assert row["user_id"] == int(row["user_id"])
        conn.execute("DELETE FROM members")
        conn.commit()
        assert bot.dm_targets(conn) == []
        assert bot.dm_targets(conn, shuffle=True) == []
        assert bot.dm_targets(conn, limit=10) == []
    finally:
        conn.close()


def test_dm_queue_filters_bots_tme_preserves_collected_order_and_full_shuffle(tmp_path, monkeypatch):
    conn = _connect(tmp_path, monkeypatch)
    try:
        # user_id и collected_at намеренно в обратном порядке: проверяем, что
        # _fetch_rows сохраняет порядок collected_at, а не rowid.
        rows = [
            (10, "Rabota", "Иван", "2026-01-05T00:00:00+00:00"),
            (20, "Subbotina", "Мария", "2026-01-01T00:00:00+00:00"),
            (30, "BotoxFan", "Олег", "2026-01-03T00:00:00+00:00"),
            (40, "SomeBot", "Бот", "2026-01-02T00:00:00+00:00"),
            (50, "normal1", "@PromoBot", "2026-01-02T00:00:00+00:00"),
            (60, "normal2", "заходи t.me/channel", "2026-01-02T00:00:00+00:00"),
            (70, "normal3", "T.ME/UPPER", "2026-01-02T00:00:00+00:00"),
        ]
        for uid, uname, fname, ts in rows:
            conn.execute(
                "INSERT INTO members(user_id,username,first_name,last_name,is_bot,"
                "collected_at,deleted,access_hash,dm_block) VALUES(?,?,?,'',0,?,0,?,NULL)",
                (uid, uname, fname, ts, uid * 7),
            )
        conn.commit()

        got = bot.dm_targets(conn)
        assert [r["user_id"] for r in got] == [20, 30, 10]

        # Добавляем 40 кандидатов и проверяем, что shuffle+limit=5 берёт цели
        # за пределами первых 5 (не отсекает раньше random.sample).
        for uid in range(100, 140):
            conn.execute(
                "INSERT INTO members(user_id,username,first_name,last_name,is_bot,"
                "collected_at,deleted,access_hash,dm_block) VALUES(?,?,?,'',0,?,0,?,NULL)",
                (uid, f"user_{uid}", "Анна", f"2026-02-{uid - 99:02d}T00:00:00+00:00", uid * 7),
            )
        conn.commit()

        first_five = {r["user_id"] for r in bot.dm_targets(conn, limit=5)}
        sampled_union = set()
        for _ in range(10):
            sampled_union |= {r["user_id"] for r in bot.dm_targets(conn, limit=5, shuffle=True)}
        assert sampled_union - first_five, "shuffle+limit должен выбирать из всей базы, а не из первых N"
    finally:
        conn.close()
