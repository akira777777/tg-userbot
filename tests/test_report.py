"""Отчёт по эффективности: разрез по кампаниям и источникам, вердикт."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import argparse

import pytest

import bot


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


def _campaign(conn, text="тест", kind="dm"):
    cur = conn.execute("INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                       (kind, text, bot.now_iso(), "main"))
    conn.commit()
    return cur.lastrowid


def _sent(conn, camp, target, replied=False, blocked=False, status="ok", offset_hours=0):
    ts = (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO sent(campaign,target,status,ts,account,replied_at,blocked_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (camp, str(target), status, ts, "main",
         ts if replied else None, ts if blocked else None))
    conn.commit()


def _member(conn, uid, source_group):
    conn.execute("INSERT OR REPLACE INTO members(user_id,username,first_name,is_bot,"
                 "collected_at,source_group) VALUES(?,?,?,0,?,?)",
                 (uid, f"u{uid}", f"U{uid}", bot.now_iso(), source_group))
    conn.commit()


def test_campaign_report_counts_rates_per_campaign(db):
    good = _campaign(db, "хороший оффер")
    bad = _campaign(db, "плохой оффер")
    for i in range(4):
        _sent(db, good, 100 + i, replied=(i == 0))
    for i in range(4):
        _sent(db, bad, 200 + i)
    report = {c["id"]: c for c in bot.campaign_report(db, days=30, min_sample=1)}
    assert report[good]["replied"] == 1
    assert round(report[good]["reply_rate"], 1) == 25.0
    assert report[good]["silent"] == 3
    assert report[bad]["replied"] == 0 and report[bad]["reply_rate"] == 0.0


def test_campaign_report_marks_low_sample(db):
    camp = _campaign(db)
    _sent(db, camp, 1)
    row = bot.campaign_report(db, days=30, min_sample=10)[0]
    assert row["low_confidence"] is True


def test_campaign_report_preview_is_single_line(db):
    camp = _campaign(db, "первая строка\nвторая строка")
    _sent(db, camp, 1)
    assert "\n" not in bot.campaign_report(db, days=30)[0]["preview"]


def test_source_report_attributes_replies_to_collection_group(db):
    conn = db
    conn.execute("INSERT OR REPLACE INTO groups(id,title,added_at) VALUES(1,'Живая',?)",
                 (bot.now_iso(),))
    conn.execute("INSERT OR REPLACE INTO groups(id,title,added_at) VALUES(2,'Мёртвая',?)",
                 (bot.now_iso(),))
    camp = _campaign(conn)
    _member(conn, 11, 1)
    _member(conn, 12, 2)
    _member(conn, 13, 2)
    _sent(conn, camp, 11, replied=True)
    _sent(conn, camp, 12)
    _sent(conn, camp, 13)
    rows = bot.source_report(conn, days=30, min_sample=1)
    by_title = {r["title"]: r for r in rows}
    assert by_title["Живая"]["replied"] == 1
    assert round(by_title["Живая"]["reply_rate"], 1) == 100.0
    assert by_title["Мёртвая"]["replied"] == 0
    assert rows[0]["title"] == "Живая"


def test_source_report_keeps_unattributed_group_separate(db):
    camp = _campaign(db)
    _sent(db, camp, 999)
    rows = bot.source_report(db, days=30, min_sample=1)
    assert rows[0]["title"] == "(без источника)"


def test_verdict_no_data_when_sample_is_small(db):
    camp = _campaign(db)
    _sent(db, camp, 1, replied=True)
    camps = bot.campaign_report(db, days=30, min_sample=10)
    assert bot.experiment_verdict(camps, min_sample=10) == "no_data"


def test_verdict_stop_copy_when_nobody_replied(db):
    camp = _campaign(db)
    for i in range(20):
        _sent(db, camp, i)
    camps = bot.campaign_report(db, days=30, min_sample=10)
    assert bot.experiment_verdict(camps, min_sample=10) == "stop_copy"


def test_verdict_scale_winner_when_one_campaign_replies(db):
    winner = _campaign(db, "работает")
    loser = _campaign(db, "не работает")
    for i in range(20):
        _sent(db, winner, i, replied=(i < 3))
    for i in range(20):
        _sent(db, loser, 100 + i)
    camps = bot.campaign_report(db, days=30, min_sample=10)
    assert bot.experiment_verdict(camps, min_sample=10) == "scale_winner"


def test_verdict_mixed_when_replies_are_below_threshold(db):
    camp = _campaign(db)
    for i in range(100):
        _sent(db, camp, i, replied=(i == 0))
    camps = bot.campaign_report(db, days=30, min_sample=10)
    assert bot.experiment_verdict(camps, min_sample=10) == "mixed"


def test_cmd_report_runs_on_empty_db(db, capsys):
    bot.cmd_report(argparse.Namespace(days=30, min_sample=10))
    out = capsys.readouterr().out
    assert "нет отправок" in out


def test_cmd_report_prints_verdict(db, capsys):
    camp = _campaign(db, "оффер")
    for i in range(12):
        _sent(db, camp, i)
    bot.cmd_report(argparse.Namespace(days=30, min_sample=10))
    out = capsys.readouterr().out
    assert "ответов нет ни на одной кампании" in out
