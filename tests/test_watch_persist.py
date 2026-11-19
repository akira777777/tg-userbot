"""Отписка «стоп» и авторегистрация группы переживают занятую базу."""

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
    conn.execute("PRAGMA busy_timeout=100")  # в тесте не ждём 15 с
    try:
        yield conn
    finally:
        conn.close()


class _Chat:
    id = 4242
    title = "Тестовая группа"
    username = "test_group"
    broadcast = False
    megagroup = True


def _holder(db):
    """Второе соединение, держащее write-транзакцию (имитация чужого воркера)."""
    import sqlite3
    conn = sqlite3.connect(bot.DB_PATH, timeout=1)
    conn.execute("PRAGMA busy_timeout=100")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO groups(id,title,username,gtype,added_at,in_broadcast,"
                 "skip_reason) VALUES(1,'x','x','group','2020-01-01T00:00:00+00:00',0,'')")
    return conn


def test_stop_request_written_after_lock_released(db):
    holder = _holder(db)
    released = {"done": False}

    def fake_sleep(_s):
        if not released["done"]:     # лок отпускается во время первого же ретрая
            holder.rollback()
            holder.close()
            released["done"] = True

    assert bot.persist_stop_request(db, 777, "victim", sleep=fake_sleep) is True
    row = db.execute("SELECT user_id, reason FROM blacklist WHERE user_id=777").fetchone()
    assert row["reason"] == "самоотписка в DM"


def test_stop_request_returns_false_when_db_never_frees(db, monkeypatch):
    holder = _holder(db)
    ok = bot.persist_stop_request(db, 778, "victim2", attempts=3, first_delay=0,
                                  sleep=lambda _s: None)
    assert ok is False
    holder.rollback(); holder.close()


def test_group_event_persisted_and_repeatable(db):
    fresh, added, can, why = bot.persist_group_event(db, "main", _Chat())
    assert fresh is True
    fresh2, added2, *_ = bot.persist_group_event(db, "main", _Chat())  # идемпотентность
    assert fresh2 is False
    row = db.execute("SELECT title, gtype FROM groups WHERE id=4242").fetchone()
    assert row["title"] == "Тестовая группа" and row["gtype"] == "megagroup"


def test_group_event_returns_none_on_persistent_lock(db):
    holder = _holder(db)
    res = bot.persist_group_event(db, "main", _Chat(), attempts=2, first_delay=0,
                                  sleep=lambda _s: None)
    assert res is None
    holder.rollback(); holder.close()


def test_persist_rolls_back_open_transaction_on_failure(db):
    holder = _holder(db)
    res = bot.persist_group_event(db, "main", _Chat(), attempts=2, first_delay=0,
                                  sleep=lambda _s: None)
    assert res is None
    assert db.in_transaction is False          # незакрытая транзакция не остаётся
    holder.rollback(); holder.close()          # убираем чужой write-лок, чтобы проверить, что conn не держит лок
    import sqlite3 as s3
    probe = s3.connect(bot.DB_PATH, timeout=1)
    probe.execute("BEGIN IMMEDIATE")            # чужой write-лок не висит
    probe.rollback(); probe.close()


def test_persist_reports_real_db_error_not_lock(db, capsys):
    db.execute("DROP TABLE blacklist")
    db.commit()
    ok = bot.persist_stop_request(db, 999, "x", attempts=2, first_delay=0,
                                  sleep=lambda _s: None)
    assert ok is False
    out = capsys.readouterr().out
    assert "no such table" in out               # реальная причина в выводе
    assert "занята" not in out                  # не маскируется под лок
