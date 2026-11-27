"""Стадия 2c ADR-0001: tguserbot.reports — дневные лимиты, здоровье, отчёты.

Пакет проверяется напрямую; связка с bot.py (cmd_status/cmd_report, циклы
отправки) покрывается существующими тестами через реэкспорт без правок.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tguserbot import reports
from tguserbot.db import connect


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
TS = NOW.isoformat(timespec="seconds")


def make_conn(tmp_path):
    conn = connect(tmp_path / "u.db", main_session="s")
    return conn


def add_campaign(conn, cid, kind="dm", account="main", text="привет"):
    conn.execute("INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(?,?,?,?,?)",
                 (cid, kind, text, TS, account))


def add_sent(conn, cid, target, status="ok", replied=False, blocked=False, msg_id=None):
    conn.execute(
        "INSERT INTO sent(campaign,target,status,ts,msg_id,replied_at,blocked_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (cid, target, status, TS, msg_id,
         TS if replied else None, TS if blocked else None))


def test_today_sent_counts_per_kind(tmp_path):
    conn = make_conn(tmp_path)
    add_campaign(conn, 1, "dm")
    add_campaign(conn, 2, "broadcast")
    add_sent(conn, 1, "10")
    add_sent(conn, 1, "11", status="fail:x")
    add_sent(conn, 2, "g1")
    conn.commit()
    assert reports.today_dm_sent(conn, "main", now=NOW) == 1
    assert reports.today_broadcast_sent(conn, "main", now=NOW) == 1
    conn.close()


def test_daily_cap_dm_capped_by_cohort_broadcast_default(tmp_path):
    conn = make_conn(tmp_path)
    # main без registered_at → консервативный unknown: потолок 20
    assert reports.daily_cap(conn, "main", "dm") == 20
    # broadcast: нет явного бюджета → дефолт 15
    assert reports.daily_cap(conn, "main", "broadcast") == 15
    conn.execute("UPDATE accounts SET broadcast_budget=7 WHERE name='main'")
    assert reports.daily_cap(conn, "main", "broadcast") == 7
    conn.close()


def test_account_dm_cap_uses_cohort_ceiling(tmp_path):
    conn = make_conn(tmp_path)
    # mature (>90 дней): потолок 60, явный бюджет 500 не поднимает
    conn.execute("UPDATE accounts SET registered_at=? WHERE name='main'",
                 ((NOW - timedelta(days=200)).isoformat(timespec="seconds"),))
    assert reports.account_dm_cap(conn, "main", configured=500, now=NOW) == 60
    # fresh (<14 дней): потолок 15, явный бюджет 10 остаётся
    conn.execute("UPDATE accounts SET registered_at=? WHERE name='main'",
                 ((NOW - timedelta(days=5)).isoformat(timespec="seconds"),))
    assert reports.account_dm_cap(conn, "main", configured=10, now=NOW) == 10
    conn.close()


def test_account_health_reports_strikes(tmp_path):
    conn = make_conn(tmp_path)
    assert reports.account_health(conn, "ghost") == "нет в реестре"
    conn.execute("UPDATE accounts SET registered_at=?, spam_strikes=2, "
                 "last_peerflood_at=? WHERE name='main'",
                 ((NOW - timedelta(days=100)).isoformat(timespec="seconds"), TS))
    text = reports.account_health(conn, "main", now=NOW)
    assert "зрелый" in text and "100 дн" in text and "PEER_FLOOD ×2" in text
    conn.close()


def test_delivery_report_rates(tmp_path):
    conn = make_conn(tmp_path)
    add_campaign(conn, 1, "dm")
    add_sent(conn, 1, "1", replied=True)
    add_sent(conn, 1, "2", blocked=True)
    add_sent(conn, 1, "3")
    add_sent(conn, 1, "4", status="fail:privacy")
    conn.commit()
    out = reports.delivery_report(conn, days=7, now=NOW)
    assert len(out) == 1
    row = out[0]
    assert row["accepted"] == 3 and row["replied"] == 1 and row["blocked"] == 1
    assert row["silent"] == 1 and row["failed"] == 1
    assert abs(row["reply_rate"] - 100 / 3) < 1e-9
    conn.close()


def test_campaign_report_low_confidence(tmp_path):
    conn = make_conn(tmp_path)
    add_campaign(conn, 1, text="текст кампании")
    add_sent(conn, 1, "1", replied=True)
    conn.commit()
    rows = reports.campaign_report(conn, days=30, now=NOW, min_sample=10)
    assert rows[0]["low_confidence"] is True
    assert rows[0]["preview"] == "текст кампании"
    conn.close()


def test_experiment_verdict_branches():
    assert reports.experiment_verdict([]) == "no_data"
    assert reports.experiment_verdict([{"accepted": 20, "replied": 0, "reply_rate": 0.0}]) == "stop_copy"
    assert reports.experiment_verdict(
        [{"accepted": 20, "replied": 3, "reply_rate": 15.0}]) == "scale_winner"
    assert reports.experiment_verdict(
        [{"accepted": 20, "replied": 1, "reply_rate": 4.9},
         {"accepted": 20, "replied": 1, "reply_rate": 4.0}]) == "mixed"


def test_campaign_stats_and_ok_count(tmp_path):
    conn = make_conn(tmp_path)
    add_campaign(conn, 1, "dm")
    add_sent(conn, 1, "1")
    add_sent(conn, 1, "2", status="skip:covered")
    add_sent(conn, 1, "3", status="fail:privacy")
    conn.commit()
    stats = reports.campaign_stats(conn, hours=168, now=NOW)
    assert stats == [{"kind": "dm", "ok": 1, "fail": 1, "skip": 1, "total": 3}]
    assert reports._campaign_ok_count(conn, 1) == 1
    assert reports._campaign_ok_count(conn, None) == 0
    conn.close()


def test_message_id_shapes():
    assert reports.message_id(SimpleNamespace(id=42)) == 42
    assert reports.message_id(SimpleNamespace(msg_id=7)) == 7
    assert reports.message_id(SimpleNamespace(message=SimpleNamespace(id=9))) == 9
    assert reports.message_id([SimpleNamespace(id=None), SimpleNamespace(id=5)]) == 5
    upd = SimpleNamespace(updates=[SimpleNamespace(message=SimpleNamespace(id=11))])
    assert reports.message_id(upd) == 11
    assert reports.message_id(SimpleNamespace()) is None
