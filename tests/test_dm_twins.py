"""Отсев твинков и очевидных ботов в личной рассылке."""

from __future__ import annotations

import pytest

import bot


def _row(**kw):
    base = {"user_id": 1, "username": "normal_user", "first_name": "Иван",
            "last_name": "Петров", "is_bot": 0}
    base.update(kw)
    return base


@pytest.mark.parametrize("row", [
    _row(first_name="Kami072895", username="Kami072895"),
    _row(username="Elainaa_74825648562", first_name="Elainaa"),
    _row(username="Willie6531", first_name="Willie"),
    _row(first_name="Fateme 👁⏳MY AGE APP$PERIA @case_rush_bot"),
    _row(first_name="Pi support"),
    _row(first_name="⏳MY AGE APP"),
    _row(first_name="Поддержка клиентов", last_name=""),
    _row(first_name=" ", last_name=""),
    _row(first_name="X", last_name=""),
    _row(first_name="👑💠", last_name=""),
    _row(username="somebot", first_name="Some"),
])
def test_junk_targets_are_rejected(row):
    ok, reason = bot.classify_dm_target(row)
    assert not ok, f"должен был отсеяться: {row}"
    assert reason in {"bot", "twin_username", "junk_name"}


@pytest.mark.parametrize("row", [
    _row(username="Subbotina", first_name="Anna Subbotina"),
    _row(username="Rabota", first_name="Мария"),
    _row(username="ChalidKurdi", first_name="Chalid Kurdi"),
    _row(username="Shaya_p78", first_name="Shaya.p"),
    _row(username="Jordi_Roses", first_name="Jordi"),
])
def test_normal_people_are_kept(row):
    ok, reason = bot.classify_dm_target(row)
    assert ok, f"не должен отсеяться: {row} -> {reason}"


def test_bot_flag_rejected():
    ok, reason = bot.classify_dm_target(_row(is_bot=1))
    assert (ok, reason) == (False, "bot")


def test_twin_detector_only_long_digit_tails():
    assert bot.member_username_is_twin("Kami072895")
    assert bot.member_username_is_twin("user12345678")
    assert bot.member_username_is_twin("Willie6531")
    # Порог — 4 цифры; короткий хвост (2 цифры) остаётся обычным ником.
    assert bot.member_username_is_twin("masha_2024")
    assert not bot.member_username_is_twin("alex99")
    assert not bot.member_username_is_twin("Shaya_p78")
    assert not bot.member_username_is_twin("")


def test_sql_predicate_excludes_twins_in_database(tmp_path, monkeypatch):
    """SQL-часть фильтра (DM_ELIGIBLE_WHERE) отсекает то же самое до выборки."""
    from bot import connect as _c  # noqa: F401
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "legacy.session"))
    conn = bot.connect()
    try:
        rows = [
            (1, "good_user", "Мария", ""),
            (2, "Kami072895", "Kamran", ""),
            (3, "abc", "Fateme ⏳MY AGE APP", ""),
            (4, "abc2", "support team", ""),
        ]
        for uid, uname, first, last in rows:
            conn.execute(
                "INSERT INTO members(user_id,username,first_name,last_name,is_bot,"
                "collected_at,access_hash) VALUES(?,?,?,?,0,?,1)",
                (uid, uname, first, last, bot.now_iso()))
        conn.commit()
        kept = {r["username"] for r in conn.execute(
            f"SELECT m.username FROM members m WHERE {bot.DM_ELIGIBLE_WHERE}")}  # nosec B608
        assert "good_user" in kept
        assert "Kami072895" not in kept
        assert "abc" not in kept
        assert "abc2" not in kept
    finally:
        conn.close()
