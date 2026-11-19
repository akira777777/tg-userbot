"""Booster: window budget enforcement, rate-limit integration, concurrency safety."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.booster import Booster, BudgetExceeded
from src.poster import Poster
from src.rate_limiter import RateLimiter


class _Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


async def test_boost_posts_while_budget_remains() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append(chat_id)
        return f"ok-{chat_id}"

    booster = Booster(
        Poster(send),
        RateLimiter(max_rate=100, period=1.0),
        budget=2,
        window=60.0,
        clock=_Clock(),
    )

    assert await booster.boost(1, "a") == "ok-1"
    assert await booster.boost(2, "b") == "ok-2"
    assert sent == [1, 2]


async def test_boost_refuses_when_budget_spent() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append(chat_id)
        return "ok"

    booster = Booster(
        Poster(send),
        RateLimiter(max_rate=100, period=1.0),
        budget=1,
        window=60.0,
        clock=_Clock(),
    )

    assert (await booster.boost(1, "a")) == "ok"
    with pytest.raises(BudgetExceeded):
        await booster.boost(2, "b")
    assert sent == [1]
    assert booster.remaining() == 0
    assert booster.can_boost() is False


async def test_budget_resets_after_window_elapses() -> None:
    clock = _Clock()
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append(chat_id)
        return "ok"

    booster = Booster(
        Poster(send),
        RateLimiter(max_rate=100, period=1.0),
        budget=1,
        window=10.0,
        clock=clock,
    )

    assert (await booster.boost(1, "a")) == "ok"
    with pytest.raises(BudgetExceeded):
        await booster.boost(2, "b")

    clock.advance(11.0)
    assert booster.remaining() == 1
    assert (await booster.boost(3, "c")) == "ok"
    assert sent == [1, 3]


async def test_can_boost_reports_readiness_without_sending() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append(chat_id)
        return "ok"

    booster = Booster(
        Poster(send),
        RateLimiter(max_rate=100, period=1.0),
        budget=1,
        window=60.0,
        clock=_Clock(),
    )

    assert booster.can_boost() is True
    await booster.boost(1, "a")
    assert booster.can_boost() is False
    assert sent == [1]


async def test_concurrent_boosts_respect_budget_exactly() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append(chat_id)
        return "ok"

    booster = Booster(
        Poster(send),
        RateLimiter(max_rate=100, period=1.0),
        budget=1,
        window=60.0,
        clock=_Clock(),
    )

    outcomes = await asyncio.gather(
        booster.boost(1, "a"),
        booster.boost(2, "b"),
        return_exceptions=True,
    )

    assert sum(isinstance(out, BudgetExceeded) for out in outcomes) == 1
    assert sum(out == "ok" for out in outcomes) == 1
    assert sent == [1] or sent == [2]


async def test_boosts_share_rate_limiter() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append(chat_id)
        return "ok"

    booster = Booster(
        Poster(send),
        RateLimiter(max_rate=2, period=1.0),
        budget=10,
        window=60.0,
        clock=_Clock(),
    )

    started = time.perf_counter()
    await asyncio.gather(*(booster.boost(i, f"m{i}") for i in (1, 2, 3)))
    elapsed = time.perf_counter() - started

    assert sorted(sent) == [1, 2, 3]
    assert elapsed >= 0.45


@pytest.mark.parametrize("budget", [-1, -5])
def test_negative_budget_rejected(budget: int) -> None:
    with pytest.raises(ValueError):
        Booster(Poster(lambda: None), RateLimiter(max_rate=1), budget=budget)


def test_nonpositive_window_rejected() -> None:
    with pytest.raises(ValueError):
        Booster(Poster(lambda: None), RateLimiter(max_rate=1), budget=1, window=0)
