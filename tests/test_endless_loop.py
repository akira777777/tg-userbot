"""Нон-стоп режим: раунды рассылки идут один за другим, пока не придёт stop.

Сеть не используется: run_once подменяется заглушкой, пауза — нулевая.
"""

from __future__ import annotations

import argparse

import bot


def _args(gap=0.0, jitter=0.0):
    return argparse.Namespace(loop_gap=gap, loop_jitter=jitter)


def test_loop_gap_is_base_plus_jitter():
    # Без разброса пауза равна базе.
    assert bot.loop_gap(1, 300, 0) == 300
    # С разбросом пауза в диапазоне [base, base+jitter].
    for _ in range(50):
        wait = bot.loop_gap(1, 300, 120)
        assert 300 <= wait <= 420


def test_loop_gap_handles_negative_values():
    assert bot.loop_gap(1, -5, -5) == 0


def test_endless_runs_rounds_until_stop(tmp_path, monkeypatch):
    """Каждый раунд вызывается снова, суммы складываются, stop прерывает."""
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    calls = {"n": 0}

    async def run_once():
        calls["n"] += 1
        return 3

    # После третьего раунда просим остановку — цикл обязан выйти.
    real_gap = bot.loop_gap

    def fake_gap(round_no, base, jitter, empty_streak=0):
        # round_no — уже выполненный раунд; после третьего просим остановку.
        if round_no >= 3 and not (tmp_path / "control-stop.json").exists():
            bot.request_stop("all")
        return 0.0

    monkeypatch.setattr(bot, "loop_gap", fake_gap)
    total = bot.run_endless(_args(), run_once)
    assert calls["n"] == 3
    assert total == 9
    monkeypatch.setattr(bot, "loop_gap", real_gap)


def test_endless_survives_empty_round_and_keeps_going(tmp_path, monkeypatch):
    """Раунд без целей (SystemExit) не обрывает нон-стоп, а уходит в паузу."""
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    seen = {"n": 0}

    async def run_once():
        seen["n"] += 1
        if seen["n"] == 1:
            raise SystemExit("Нет целей (всё отправлено/покрыто)")
        if seen["n"] >= 3:
            bot.request_stop("all")
        return 0

    def fake_gap(round_no, base, jitter, empty_streak=0):
        if round_no >= 3:
            bot.request_stop("all")
        return 0.0

    monkeypatch.setattr(bot, "loop_gap", fake_gap)
    total = bot.run_endless(_args(), run_once)
    assert seen["n"] == 3
    assert total == 0


def test_endless_survives_network_exception(tmp_path, monkeypatch):
    """Сбой сети в раунде не роняет процесс — следующий раунд идёт."""
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    seen = {"n": 0}

    async def run_once():
        seen["n"] += 1
        if seen["n"] == 1:
            raise ConnectionError("telegram down")
        bot.request_stop("all")
        return 2

    monkeypatch.setattr(bot, "loop_gap", lambda r, b, j, s=0: 0.0)
    total = bot.run_endless(_args(), run_once)
    assert seen["n"] == 2
    assert total == 2


def test_stop_requested_before_first_round(tmp_path, monkeypatch):
    """Если stop уже стоит на старте, ни один раунд не запускается."""
    stop = tmp_path / "control-stop.json"
    monkeypatch.setattr(bot, "STOP_PATH", stop)
    bot.request_stop("all")
    # now_iso() у остановки >= started_at внутри run_endless
    called = {"n": 0}

    async def run_once():
        called["n"] += 1
        return 1

    monkeypatch.setattr(bot, "loop_gap", lambda r, b, j, s=0: 0.0)
    total = bot.run_endless(_args(), run_once)
    assert called["n"] == 0
    assert total == 0

def test_loop_gap_grows_on_empty_rounds():
    """Пустые раунды наращивают паузу, чтобы не молотить Telegram вхолостую."""
    base = bot.loop_gap(1, 300, 0, 0)
    one = bot.loop_gap(1, 300, 0, 1)
    two = bot.loop_gap(1, 300, 0, 2)
    assert base == 300
    assert one == 600
    assert two == 1200


def test_loop_gap_caps_empty_backoff():
    """Экспонента не уходит в бесконечность: есть потолок."""
    wait = bot.loop_gap(1, 300, 0, 20)
    assert wait <= max(300, bot.LOOP_EMPTY_CAP)


def test_loop_gap_resets_after_success():
    """Успешный раунд возвращает обычный интервал (streak=0)."""
    assert bot.loop_gap(5, 300, 0, 0) == 300