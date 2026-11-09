"""Сгруппированный gather совпадает с построчными счётчиками и не выгружает ники."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import cloud


SCHEMA = """
CREATE TABLE accounts(
  name TEXT PRIMARY KEY, user_id INTEGER, api_id TEXT, enabled INTEGER,
  daily_budget INTEGER, flood_until TEXT, note TEXT
);
CREATE TABLE groups(
  id INTEGER PRIMARY KEY, title TEXT, username TEXT, gtype TEXT,
  in_broadcast INTEGER, active INTEGER
);
CREATE TABLE account_groups(
  account TEXT, group_id INTEGER, can_write INTEGER, seen_at TEXT,
  PRIMARY KEY(account, group_id)
);
CREATE TABLE members(user_id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE member_sources(user_id INTEGER, group_id INTEGER, PRIMARY KEY(user_id, group_id));
CREATE TABLE campaigns(
  id INTEGER PRIMARY KEY, kind TEXT, account TEXT, text TEXT, created_at TEXT
);
CREATE TABLE sent(
  campaign INTEGER, target TEXT, account TEXT, status TEXT, ts TEXT,
  PRIMARY KEY(campaign, target)
);
CREATE TABLE blacklist(user_id INTEGER PRIMARY KEY, username TEXT, reason TEXT, ts TEXT);
CREATE TABLE media(id INTEGER PRIMARY KEY);
"""


def test_gather_matches_grouped_counts_and_skips_members(tmp_path, monkeypatch):
    db_path = tmp_path / "mirror.db"
    monkeypatch.setattr(cloud, "DB", db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = now.isoformat(timespec="seconds")
    yesterday = (now - timedelta(days=1)).isoformat(timespec="seconds")

    con.execute("INSERT INTO accounts VALUES('main', 10, '1', 1, 30, NULL, '')")
    con.execute("INSERT INTO accounts VALUES('work2', 20, '2', 1, 15, NULL, '')")
    con.executemany(
        "INSERT INTO groups VALUES(?,?,?,?,?,?)",
        [
            (10, "Yes", "yes", "megagroup", 1, 1),
            (20, "Mixed", "mixed", "megagroup", 1, 1),
            (30, "No", "no", "group", 0, 1),
        ],
    )
    con.executemany(
        "INSERT INTO account_groups VALUES(?,?,?,?)",
        [
            ("main", 10, 1, today),
            ("work2", 10, 1, today),
            ("main", 20, 1, today),
            ("work2", 20, 0, today),
            ("main", 30, 0, today),
        ],
    )
    con.execute("INSERT INTO blacklist VALUES(9, 'hidden-user', 'manual', ?)", (today,))
    con.execute("INSERT INTO members VALUES(1, 'secret-nick-not-for-cloud')")
    con.execute("INSERT INTO members VALUES(2, 'other-secret')")
    con.executemany("INSERT INTO member_sources VALUES(?,?)", [(1, 10), (2, 10), (1, 20)])
    con.executemany(
        "INSERT INTO campaigns VALUES(?,?,?,?,?)",
        [
            (7, "dm", "main", "a", today),
            (8, "dm", "work2", "b", yesterday),
            (9, "broadcast", "work2", "c" * 600, today),
        ],
    )
    con.executemany(
        "INSERT INTO sent VALUES(?,?,?,?,?)",
        [
            (7, "5", "main", "ok", today),
            (7, "4", "main", "fail:x", today),
            (8, "6", "work2", "ok", yesterday),
            (9, "10", "work2", "ok", today),
        ],
    )
    con.commit()

    try:
        out = cloud.gather(con)
        assert "tb_members" not in out
        encoded = json.dumps(out, ensure_ascii=False)
        assert "secret-nick-not-for-cloud" not in encoded
        assert "hidden-user" not in encoded
        assert "api_id" not in encoded

        accounts = {row["name"]: row for row in out["tb_accounts"][0]}
        assert (accounts["main"]["groups_covered"], accounts["main"]["writable_groups"], accounts["main"]["dm_today"]) == (3, 2, 1)
        assert (accounts["work2"]["groups_covered"], accounts["work2"]["writable_groups"], accounts["work2"]["dm_today"]) == (2, 1, 0)

        groups = {row["id"]: row for row in out["tb_groups"][0]}
        assert groups[10]["can_write_agg"] == "yes"
        assert groups[10]["members_collected"] == 2
        assert groups[20]["can_write_agg"] == "mixed"
        assert groups[20]["members_collected"] == 1
        assert groups[30]["can_write_agg"] == "no"
        assert groups[30]["members_collected"] == 0

        campaigns = out["tb_campaigns"][0]
        assert [row["id"] for row in campaigns] == [9, 8, 7]
        by_id = {row["id"]: row for row in campaigns}
        assert (by_id[7]["ok"], by_id[7]["fail"]) == (1, 1)
        assert (by_id[8]["ok"], by_id[8]["fail"]) == (1, 0)
        assert (by_id[9]["ok"], by_id[9]["fail"]) == (1, 0)
        assert len(by_id[9]["text"]) == 500
        assert len(out["tb_sent"][0]) == 4
    finally:
        con.close()
