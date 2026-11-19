"""main.py CLI: wiring test for RateLimiter + Poster + Booster (+ Scheduler)."""

from __future__ import annotations

import pytest

from src.booster import BudgetExceeded, Booster
from src.main import build_booster, main


def test_build_booster_wires_full_stack() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append(chat_id)
        return f"ok-{chat_id}"

    booster = build_booster(send, max_rate=100.0, budget=2, window=60.0)

    assert isinstance(booster, Booster)
    assert booster.can_boost() is True

    import asyncio

    assert asyncio.run(booster.boost(1, "a")) == "ok-1"
    assert asyncio.run(booster.boost(2, "b")) == "ok-2"
    with pytest.raises(BudgetExceeded):
        asyncio.run(booster.boost(3, "c"))
    assert sent == [1, 2]
    assert booster.can_boost() is False


def test_main_runs_one_boost_and_returns_zero() -> None:
    exit_code = main(["--chat-id", "7", "--text", "hello", "--budget", "1"])
    assert exit_code == 0


def test_main_requires_chat_id() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--text", "hello"])
    assert exc.value.code == 2


def test_main_rejects_negative_budget() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--chat-id", "1", "--text", "x", "--budget", "-1"])
    assert exc.value.code == 2
