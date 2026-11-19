"""Engagement booster: posts through a Poster under a window budget and shared
rate limit."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

__all__ = ["Booster", "BudgetExceeded"]


class BudgetExceeded(Exception):
    """Raised when the boosting budget for the current window is spent."""


class Booster:
    """Sends ``poster.post(chat_id, text)`` while a sliding window budget allows
    and the shared rate limiter grants tokens.

    Spends are recorded per window of ``window`` seconds (injectable ``clock``
    for deterministic tests). ``budget=0`` disables boosting entirely.
    Concurrency-safe: concurrent ``boost`` calls never overspend the budget.
    """

    def __init__(
        self,
        poster: Any,
        rate_limiter: Any,
        *,
        budget: int = 0,
        window: float = 86400.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if budget < 0:
            raise ValueError("budget must be >= 0")
        if window <= 0:
            raise ValueError("window must be > 0")
        self._poster = poster
        self._limiter = rate_limiter
        self.budget = budget
        self.window = float(window)
        self._clock = clock or time.monotonic
        self._spends: list[float] = []
        self._lock = asyncio.Lock()

    def spent(self, now: Optional[float] = None) -> int:
        """Spend count inside the current window (prunes expired entries)."""
        ts = self._clock() if now is None else now
        self._spends = [t for t in self._spends if t > ts - self.window]
        return len(self._spends)

    def remaining(self, now: Optional[float] = None) -> int:
        return max(self.budget - self.spent(now), 0)

    def can_boost(self) -> bool:
        """True when the window budget is not yet spent (no side effects)."""
        return self.remaining() > 0

    async def boost(self, chat_id: Any, text: str) -> Any:
        """Post once. Raises :class:`BudgetExceeded` when the window budget is
        spent, and :class:`PostError` when delivery ultimately fails."""
        async with self._lock:
            if self.remaining() <= 0:
                raise BudgetExceeded(f"budget {self.budget} spent for window {self.window}s")
            await self._limiter.acquire()
            message_id = await self._poster.post(chat_id, text)
            self._spends.append(self._clock())
            return message_id
