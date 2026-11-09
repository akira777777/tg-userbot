"""Воркер не выполняет команду без аренды и завершает её один раз."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control.config import ControlSettings
from control.models import CommandKind, CommandMode, CommandPayload, CommandRecord, CommandStatus, ReadPayload
from control.queue import LeaseRenewal
from control.worker import ControlWorker


def _settings() -> ControlSettings:
    return ControlSettings(
        bot_token="token",
        mini_app_url="https://example.test/app",
        owner_telegram_ids=frozenset({1}),
        claim_batch_size=5,
        poll_interval_seconds=2.0,
    )


def _record() -> CommandRecord:
    return CommandRecord(
        id=uuid4(),
        request=CommandPayload(
            kind=CommandKind.STATUS,
            mode=CommandMode.READ,
            account="main",
            payload=ReadPayload(),
        ),
        requested_by=1,
        status=CommandStatus.CLAIMED,
        created_at=datetime.now(UTC),
        version=1,
    )


class _Queue:
    def __init__(self, renewal: LeaseRenewal | None) -> None:
        self.renewal = renewal
        self.completed: list[dict] = []
        self.events: list[dict] = []

    def renew_lease(self, **_kwargs):
        return self.renewal

    def complete(self, **kwargs) -> bool:
        self.completed.append(kwargs)
        return True

    def fail(self, **kwargs) -> bool:
        self.completed.append(kwargs)
        return True

    def append_event(self, **kwargs) -> bool:
        self.events.append(kwargs)
        return True


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0

    async def run_command(self, _record):
        self.calls += 1
        return {"ok": True, "output": "done", "redacted_account": "Аккаунт 1"}


@pytest.mark.asyncio
async def test_lost_lease_does_not_run_command():
    worker = ControlWorker(_settings())
    runtime = _Runtime()
    worker._queue = _Queue(None)
    worker._runtime = runtime
    await worker._process(_record())
    assert runtime.calls == 0
    assert worker._queue.completed == []


@pytest.mark.asyncio
async def test_successful_command_completes_once_with_terminal_sequence():
    worker = ControlWorker(_settings())
    runtime = _Runtime()
    worker._queue = _Queue(LeaseRenewal(version=2, lease_expires_at=None))
    worker._runtime = runtime
    await worker._process(_record())
    assert runtime.calls == 1
    assert len(worker._queue.completed) == 1
    assert worker._queue.completed[0]["status"] == CommandStatus.SUCCEEDED
    assert worker._queue.events[0]["sequence"] == 2


@pytest.mark.asyncio
async def test_idle_queue_sleeps_and_full_batch_does_not(monkeypatch: pytest.MonkeyPatch):
    worker = ControlWorker(_settings())
    slept: list[float] = []

    async def idle_tick() -> int:
        return 0

    async def stop_after_sleep(seconds: float) -> None:
        slept.append(seconds)
        worker._running = False

    worker._tick = idle_tick
    monkeypatch.setattr("control.worker.asyncio.sleep", stop_after_sleep)
    await worker.run()
    assert slept == [2.0]

    worker = ControlWorker(_settings())
    state = {"n": 0}

    async def full_then_stop() -> int:
        state["n"] += 1
        if state["n"] >= 2:
            worker._running = False
            return 0
        return worker._settings.claim_batch_size

    worker._tick = full_then_stop
    busy_slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        busy_slept.append(seconds)

    monkeypatch.setattr("control.worker.asyncio.sleep", record_sleep)
    await worker.run()
    assert busy_slept == []
    assert state["n"] == 2
