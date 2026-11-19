"""Scheduler core: interval jobs, one-shot jobs, cancel, run loop, injectable clock."""

from __future__ import annotations

import asyncio

import pytest

from src.scheduler_core import Scheduler


class _Clock:
    """Manual monotonic clock for deterministic scheduler tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


def test_every_job_fires_on_each_interval() -> None:
    clock = _Clock()
    scheduler = Scheduler(clock=clock)
    fired: list[str] = []

    scheduler.every(1.0, fired.append, "tick")

    clock.advance(1.0)
    assert scheduler.run_pending() == 1
    assert fired == ["tick"]
    clock.advance(1.0)
    assert scheduler.run_pending() == 1
    assert fired == ["tick", "tick"]
    assert scheduler.pending() == 1


def test_once_job_fires_exactly_once() -> None:
    clock = _Clock()
    scheduler = Scheduler(clock=clock)
    fired: list[str] = []

    scheduler.once(0.5, fired.append, "once")

    clock.advance(0.5)
    assert scheduler.run_pending() == 1
    assert fired == ["once"]
    assert scheduler.pending() == 0

    clock.advance(10.0)
    assert scheduler.run_pending() == 0
    assert fired == ["once"]


def test_cancel_prevents_firing_and_is_idempotent() -> None:
    clock = _Clock()
    scheduler = Scheduler(clock=clock)
    fired = []
    job = scheduler.once(1.0, fired.append)

    assert scheduler.cancel(job) is True
    assert scheduler.cancel(job) is False

    clock.advance(10.0)
    assert scheduler.run_pending() == 0
    assert fired == []
    assert scheduler.pending() == 0


def test_interval_job_keeps_fixed_schedule() -> None:
    clock = _Clock()
    scheduler = Scheduler(clock=clock)
    fired_at: list[float] = []

    scheduler.every(2.0, lambda: fired_at.append(clock()))

    for _ in range(3):
        clock.advance(2.0)
        assert scheduler.run_pending() == 1
    assert fired_at == [2.0, 4.0, 6.0]


async def test_run_loop_fires_jobs_until_stopped() -> None:
    scheduler = Scheduler()
    fired = []

    scheduler.every(0.01, lambda: fired.append(1))
    task = asyncio.create_task(scheduler.run())

    await asyncio.sleep(0.06)
    scheduler.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert len(fired) >= 2


async def test_run_loop_stops_without_jobs() -> None:
    scheduler = Scheduler()
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.03)
    scheduler.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.parametrize("interval", [0, -1])
def test_invalid_interval_rejected(interval: float) -> None:
    with pytest.raises(ValueError):
        Scheduler().every(interval, lambda: None)


def test_negative_delay_rejected() -> None:
    with pytest.raises(ValueError):
        Scheduler().once(-1, lambda: None)
