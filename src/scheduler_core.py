"""Minimal asyncio scheduler: interval and one-shot jobs on an injectable clock."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = ["Scheduler", "Job"]


@dataclass
class Job:
    """A scheduled callback. Create via ``Scheduler.every`` / ``Scheduler.once``."""

    func: Callable[..., Any]
    interval: Optional[float]  # None => one-shot
    next_due: float
    args: tuple = ()
    name: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancelled: bool = False
    fires: int = field(default=0, repr=False)


class Scheduler:
    """Runs jobs on a clock (``time.monotonic`` by default; pass ``clock`` for
    deterministic tests).

    ``run_pending()`` fires every due job at most once per call. Interval jobs
    keep a fixed schedule: a job whose due time has passed is fired once on the
    next call and rescheduled one interval ahead (no runaway catch-up burst).
    ``run()`` loops until ``stop()``, sleeping in real time.
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock or time.monotonic
        self._jobs: list[Job] = []
        self._stop = asyncio.Event()

    def every(self, interval: float, func: Callable[..., Any], *args: Any, name: Optional[str] = None) -> Job:
        """Schedule ``func(*args)`` to fire every ``interval`` seconds."""
        if interval <= 0:
            raise ValueError("interval must be > 0")
        return self._add(func, interval=interval, args=args, name=name)

    def once(self, delay: float, func: Callable[..., Any], *args: Any, name: Optional[str] = None) -> Job:
        """Schedule ``func(*args)`` to fire a single time after ``delay`` seconds."""
        if delay < 0:
            raise ValueError("delay must be >= 0")
        return self._add(func, interval=None, args=args, name=name, delay=delay)

    def _add(self, func, *, interval, args, name, delay: Optional[float] = None) -> Job:
        job = Job(
            func=func,
            interval=interval,
            next_due=self._clock() + (delay if delay is not None else interval),
            args=tuple(args),
            name=name,
        )
        self._jobs.append(job)
        return job

    def cancel(self, job: Job) -> bool:
        """Cancel ``job``; returns False if it was already cancelled."""
        if job.cancelled:
            return False
        job.cancelled = True
        return True

    def pending(self) -> int:
        return sum(1 for j in self._jobs if not j.cancelled)

    def run_pending(self, now: Optional[float] = None) -> int:
        """Fire all currently due jobs at most once each; returns the fired count."""
        ts = self._clock() if now is None else now
        fired = 0
        for job in self._jobs:
            if job.cancelled or job.next_due > ts:
                continue
            job.func(*job.args)
            job.fires += 1
            fired += 1
            if job.interval is None:
                job.cancelled = True
            else:
                job.next_due += job.interval
        self._jobs = [j for j in self._jobs if not j.cancelled]
        return fired

    async def run(self, tick: float = 0.01) -> None:
        """Loop until ``stop()``; fire due jobs and sleep until the next due time."""
        while not self._stop.is_set():
            now = self._clock()
            self.run_pending(now)
            next_due = min((j.next_due for j in self._jobs if not j.cancelled), default=None)
            if next_due is None:
                wait = tick
            else:
                wait = min(max(next_due - now, 0.0), tick)
            await asyncio.sleep(wait)

    def stop(self) -> None:
        """Signal ``run()`` to return after the current iteration."""
        self._stop.set()

    async def __aenter__(self) -> "Scheduler":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.stop()
        return None
