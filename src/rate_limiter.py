"""Token-bucket rate limiter for asyncio code."""

from __future__ import annotations

import asyncio
import time

__all__ = ["RateLimiter"]


class RateLimiter:
    """Async token bucket: ``max_rate`` tokens refill over ``period`` seconds.

    ``acquire()`` blocks until the requested number of tokens is available;
    ``acquire(block=False)`` probes without waiting. Instances are safe to
    share between concurrent tasks.
    """

    def __init__(self, max_rate: float, period: float = 1.0) -> None:
        if max_rate <= 0:
            raise ValueError("max_rate must be > 0")
        if period <= 0:
            raise ValueError("period must be > 0")
        self.max_rate = float(max_rate)
        self.period = float(period)
        self._tokens = self.max_rate
        self._refilled_at = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._refilled_at
        if elapsed > 0:
            self._tokens = min(self.max_rate, self._tokens + elapsed * self.max_rate / self.period)
            self._refilled_at = now

    async def acquire(self, tokens: float = 1.0, *, block: bool = True) -> bool:
        """Consume ``tokens`` if available; with ``block=True``, wait until they are.

        Returns True when tokens were consumed, False when ``block=False`` and
        the bucket is short.
        """
        if tokens <= 0:
            return True
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                if not block:
                    return False
                shortfall = tokens - self._tokens
                await asyncio.sleep(shortfall * self.period / self.max_rate)

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def available(self) -> float:
        """Current token balance after refill."""
        self._refill()
        return self._tokens
