"""Token-bucket rate limiter: burst capacity, blocking refill, non-blocking probe."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.rate_limiter import RateLimiter


async def test_burst_within_capacity_succeeds_immediately() -> None:
    limiter = RateLimiter(max_rate=10, period=1.0)
    started = time.perf_counter()
    for _ in range(10):
        assert await limiter.acquire() is True
    assert time.perf_counter() - started < 0.3


async def test_acquire_waits_until_bucket_refills() -> None:
    limiter = RateLimiter(max_rate=2, period=1.0)
    await limiter.acquire()
    await limiter.acquire()
    started = time.perf_counter()
    assert await limiter.acquire() is True
    assert time.perf_counter() - started >= 0.45


async def test_nonblocking_acquire_refuses_when_empty() -> None:
    limiter = RateLimiter(max_rate=1, period=1.0)
    assert await limiter.acquire() is True
    assert await limiter.acquire(block=False) is False
    assert limiter.available() < 1.0


async def test_bucket_recovers_after_period_elapses() -> None:
    limiter = RateLimiter(max_rate=1, period=0.1)
    await limiter.acquire()
    await asyncio.sleep(0.12)
    assert await limiter.acquire(block=False) is True


async def test_async_context_manager_acquires_tokens() -> None:
    limiter = RateLimiter(max_rate=1, period=1.0)
    async with limiter:
        pass
    assert await limiter.acquire(block=False) is False


@pytest.mark.parametrize("max_rate", [0, -1])
def test_invalid_rate_rejected(max_rate: float) -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_rate=max_rate)


def test_invalid_period_rejected() -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_rate=1, period=0)
