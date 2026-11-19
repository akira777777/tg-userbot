"""db_call_retry: лок SQLite не должен ронять запись, чужие ошибки — должны падать."""

import sqlite3

import pytest

import bot


def _locked():
    return sqlite3.OperationalError("database is locked")


def test_transient_locks_are_retried():
    calls = []
    sleeps = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _locked()
        return "ok"

    assert bot.db_call_retry(flaky, first_delay=0.01, sleep=sleeps.append) == "ok"
    assert len(calls) == 3
    assert len(sleeps) == 2          # спим только между попытками


def test_backoff_grows_and_caps():
    sleeps = []

    def always_locked():
        raise _locked()

    with pytest.raises(sqlite3.OperationalError):
        bot.db_call_retry(always_locked, attempts=4, first_delay=1.0, max_delay=4.0,
                          sleep=sleeps.append)
    assert sleeps == [pytest.approx(1.0, abs=0.5), pytest.approx(2.0, abs=1.0),
                      pytest.approx(4.0, abs=2.0)]  # 1, 2, 4 (+джиттер <=0.15)


def test_non_lock_operational_error_reraised_immediately():
    def boom():
        raise sqlite3.OperationalError("no such table: groups")

    calls = []
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        bot.db_call_retry(boom, sleep=lambda s: calls.append(s))
    assert calls == []              # без повторов и без сна


def test_gives_up_after_attempts():
    calls = []

    def always_locked():
        calls.append(1)
        raise _locked()

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        bot.db_call_retry(always_locked, attempts=3, first_delay=0, sleep=lambda s: None)
    assert len(calls) == 3
