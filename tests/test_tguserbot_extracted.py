"""Стадия 1 ADR-0001: чистые хелперы в tguserbot — бюджеты и паузы.

Пакет проверяется напрямую, без импорта bot.py; сценарии частично дублируют
tests/test_antispam_policy.py и tests/test_both_conduct.py, которые ходят через
реэкспорт bot — так фиксируется совпадение поведения обоих входов.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tguserbot.budget import COHORT_BUDGET, account_cohort, cohort_budget, trim_to_cap
from tguserbot.pacing import (
    BOTH_SLEEP_CAP,
    earliest_moment,
    flood_pause_until,
    flood_wait_seconds,
    next_utc_midnight,
    ordered_pause,
    peerflood_backoff,
    peerflood_pause_until,
    wait_seconds,
)


class Flood:
    def __init__(self, seconds):
        self.seconds = seconds


# --- бюджеты ------------------------------------------------------------------


def test_account_cohort_thresholds():
    assert account_cohort(0) == "fresh"
    assert account_cohort(13) == "fresh"
    assert account_cohort(14) == "warm"
    assert account_cohort(89) == "warm"
    assert account_cohort(90) == "mature"
    assert account_cohort(None) == "unknown"
    assert account_cohort("мусор") == "unknown"
    assert account_cohort(1000, "fresh") == "fresh"
    assert account_cohort(1000, "auto") == "mature"


def test_cohort_budget_caps_manual_budget():
    assert cohort_budget("fresh") == 15
    assert cohort_budget("fresh", 10) == 10
    assert cohort_budget("fresh", 500) == 15
    assert cohort_budget("warm", 0) == 30
    assert cohort_budget("warm", None) == 30
    assert cohort_budget("нет_такого") == COHORT_BUDGET["unknown"]


def test_trim_to_cap():
    rows = [1, 2, 3, 4, 5]
    assert trim_to_cap(rows, 0, 0) == (rows, None)
    assert trim_to_cap(rows, 3, 0) == ([1, 2, 3], 3)
    assert trim_to_cap(rows, 3, 5) == ([], 0)
    assert trim_to_cap(rows, 10, 2) == (rows, 8)


# --- паузы и тайминги ----------------------------------------------------------


def test_ordered_pause_swaps_and_clamps():
    assert ordered_pause(8, 3) == (3.0, 8.0)
    assert ordered_pause(-5, 2) == (0.0, 2.0)
    assert ordered_pause(None, None) == (0.0, 0.0)


def test_flood_wait_seconds_jitter_and_floor():
    assert flood_wait_seconds(Flood(100)) == 115
    assert flood_wait_seconds(Flood(10), jitter=1.0) == 10
    assert flood_wait_seconds(Flood(0)) >= 1
    assert flood_wait_seconds(object()) >= 1  # без атрибута seconds
    assert flood_wait_seconds(Flood(10 ** 9)) == 86400


def test_flood_pause_until_uses_fixed_now():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    until = flood_pause_until(Flood(60), now)
    assert datetime.fromisoformat(until) >= now + timedelta(seconds=60)


def test_peerflood_backoff_exponential_with_cap():
    assert peerflood_backoff(1) == timedelta(hours=24)
    assert peerflood_backoff(2) == timedelta(hours=48)
    assert peerflood_backoff(20) == timedelta(days=30)
    assert peerflood_backoff(0) == timedelta(hours=24)


def test_peerflood_pause_until_uses_fixed_now():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert datetime.fromisoformat(peerflood_pause_until(1, now)) == now + timedelta(hours=24)


def test_earliest_moment_mixed_deadlines():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    future_aware = now + timedelta(minutes=5)
    future_naive = datetime(2026, 11, 20, 12, 10)
    past = "2026-11-20T11:00:00+00:00"
    garbage = "не дата"
    assert earliest_moment(now, [future_aware, future_naive, past, garbage, None]) == future_aware
    assert earliest_moment(now, [past, None]) is None


def test_next_utc_midnight_and_wait_seconds():
    now = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)
    assert next_utc_midnight(now) == datetime(2026, 11, 21, tzinfo=timezone.utc)
    assert wait_seconds(now, None) == float(BOTH_SLEEP_CAP)
    assert wait_seconds(now, now - timedelta(seconds=5)) == 0.0
    assert wait_seconds(now, now + timedelta(hours=5)) == float(BOTH_SLEEP_CAP)
    assert wait_seconds(now, now + timedelta(seconds=40), cap=50) == 40.0
