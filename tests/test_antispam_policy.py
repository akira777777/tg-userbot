"""Анти-спам политика: jitter пауз, backoff PEER_FLOOD, контингенты и бюджеты.

Доктрина — docs/telegram/limits-and-antispam.md. Тесты офлайн: только чистая
логика и временная SQLite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import bot


class Flood(Exception):
    def __init__(self, seconds):
        super().__init__(f"FLOOD_WAIT_{seconds}")
        self.seconds = seconds


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    monkeypatch.setattr(bot, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "sessions" / "main.session"))
    monkeypatch.setattr(bot, "SECRETS_PATH", tmp_path / "account-secrets.json")
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    connection = bot.connect()
    try:
        yield connection
    finally:
        connection.close()


def test_flood_wait_adds_jitter_and_never_goes_below_one():
    assert bot.flood_wait_seconds(Flood(100)) == 115
    # FLOOD_WAIT_0 не должен превращаться в пустой цикл.
    assert bot.flood_wait_seconds(Flood(0)) >= 1
    assert bot.flood_wait_seconds(Flood(10), jitter=1.0) == 10
    assert bot.flood_wait_seconds(Flood(10 ** 9)) == 86400


def test_flood_pause_until_respects_server_seconds():
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    until = datetime.fromisoformat(bot.flood_pause_until(Flood(60), now))
    assert timedelta(seconds=60) < until - now <= timedelta(seconds=90)


def test_peerflood_backoff_doubles_up_to_thirty_days():
    assert bot.peerflood_backoff(1) == timedelta(hours=24)
    assert bot.peerflood_backoff(2) == timedelta(hours=48)
    assert bot.peerflood_backoff(3) == timedelta(hours=96)
    assert bot.peerflood_backoff(20) == timedelta(days=30)
    # 0/None трактуем как первый страйк, а не как «без паузы».
    assert bot.peerflood_backoff(0) == timedelta(hours=24)


def test_peerflood_lasts_longer_than_a_flood_wait():
    exc = Flood(30)
    now = datetime.now(timezone.utc)
    flood_until = datetime.fromisoformat(bot.flood_pause_until(exc, now))
    spam_until = datetime.fromisoformat(bot.peerflood_pause_until(1, now))
    assert spam_until - flood_until > timedelta(hours=23)


@pytest.mark.parametrize("age,cohort", [
    (None, "unknown"),
    (0, "fresh"),
    (13.9, "fresh"),
    (14, "warm"),
    (89, "warm"),
    (90, "mature"),
    (4000, "mature"),
])
def test_account_cohort_by_age(age, cohort):
    assert bot.account_cohort(age) == cohort


def test_explicit_cohort_overrides_age():
    assert bot.account_cohort(1000, "fresh") == "fresh"
    assert bot.account_cohort(None, "auto") == "unknown"


def test_cohort_budget_caps_a_manual_budget_but_keeps_a_smaller_one():
    assert bot.cohort_budget("fresh") == 15
    assert bot.cohort_budget("mature") == 60
    # Явный меньший бюджет уважается, больший — нет: возраст флагом не обходится.
    assert bot.cohort_budget("fresh", 10) == 10
    assert bot.cohort_budget("fresh", 500) == 15
    assert bot.cohort_budget("mature", 30) == 30
    # 0/NULL = «по умолчанию», а не «без лимита».
    assert bot.cohort_budget("warm", 0) == 30
    assert bot.cohort_budget("warm", None) == 30


def test_peerflood_strikes_accumulate_and_extend_cooldown(isolated):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    first = bot.register_peerflood(isolated, "main", now)
    row = isolated.execute(
        "SELECT spam_strikes, flood_until, last_peerflood_at FROM accounts WHERE name='main'"
    ).fetchone()
    assert first == 1 and row["spam_strikes"] == 1
    assert datetime.fromisoformat(row["flood_until"]) - now == timedelta(hours=24)

    second = bot.register_peerflood(isolated, "main", now)
    row = isolated.execute(
        "SELECT spam_strikes, flood_until FROM accounts WHERE name='main'").fetchone()
    assert second == 2 and row["spam_strikes"] == 2
    assert datetime.fromisoformat(row["flood_until"]) - now == timedelta(hours=48)


def test_account_dm_cap_uses_registration_date(isolated):
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    fresh = (now - timedelta(days=5)).isoformat(timespec="seconds")
    mature = (now - timedelta(days=400)).isoformat(timespec="seconds")
    isolated.execute("INSERT INTO accounts(name,session,added_at,daily_budget) "
                     "VALUES('fresh','s',?,NULL)", (bot.now_iso(),))
    isolated.execute("INSERT INTO accounts(name,session,added_at,daily_budget) "
                     "VALUES('mature','s',?,NULL)", (bot.now_iso(),))
    isolated.execute("UPDATE accounts SET registered_at=? WHERE name='fresh'", (fresh,))
    isolated.execute("UPDATE accounts SET registered_at=? WHERE name='mature'", (mature,))
    isolated.commit()
    assert bot.account_dm_cap(isolated, "fresh", None, now) == 15
    assert bot.account_dm_cap(isolated, "mature", None, now) == 60
    assert bot.account_dm_cap(isolated, "nobody", None, now) == 20


def test_account_health_mentions_when_registration_is_unknown(isolated):
    assert "неизвестна" in bot.account_health(isolated, "main")


def test_restricted_account_keeps_dm_channel_closed():
    now = datetime.now(timezone.utc)
    until = (now + timedelta(hours=5)).isoformat(timespec="seconds")
    player = {"flood_until": until, "spam_strikes": 1,
              "dm_budget": 10, "dm_used": 0}
    assert bot.channel_open(player, "dm", now) is False
    # Тот же аккаунт может продолжать рассылку в чаты: ограничение — на личные.
    assert bot.channel_open(player, "broadcast", now) is True
    # Без страйка общий cooldown на DM не распространяется.
    assert bot.channel_open({**player, "spam_strikes": 0}, "dm", now) is True


def test_user_restricted_is_a_spam_signal_not_a_flood_wait():
    from telethon.errors import UserRestrictedError

    exc = UserRestrictedError(request=None)
    assert bot.send_error_class(exc) == "spam"
    assert bot.target_disposition(exc, "dm") == "halt_dm_queue"
