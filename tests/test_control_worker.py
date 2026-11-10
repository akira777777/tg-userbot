"""Воркер не выполняет команду без аренды и завершает её один раз."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control.config import ControlSettings
from control.models import (
    BroadcastPayload,
    CommandKind,
    CommandMode,
    CommandPayload,
    CommandRecord,
    CommandStatus,
    ReadPayload,
    StopPayload,
)
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

    def cancel(self, command_id, _worker_id) -> bool:
        self.completed.append({"cancelled": command_id})
        return True

    def claim_commands(self, **_kwargs):
        return []


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0

    async def run_command(self, _record):
        self.calls += 1
        return {"ok": True, "output": "done", "redacted_account": "Аккаунт 1"}

    async def terminate_command(self, _command_id):
        return False


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
async def test_policy_rejects_non_owner_before_execution():
    worker = ControlWorker(_settings())
    runtime = _Runtime()
    worker._queue = _Queue(LeaseRenewal(version=2, lease_expires_at=None))
    worker._runtime = runtime
    record = _record()
    record = record.model_copy(update={"requested_by": 99})
    await worker._process(record)
    assert runtime.calls == 0
    assert worker._queue.completed[0]["error_code"] == "policy"
    assert worker._queue.completed[0]["result_summary"]["ok"] is False


@pytest.mark.asyncio
async def test_lost_lease_during_execution_does_not_complete():
    worker = ControlWorker(_settings())
    worker._lease_renew_interval = 0.01

    class _SlowRuntime(_Runtime):
        async def run_command(self, _record):
            await asyncio.sleep(0.05)
            self.calls += 1
            return {"ok": True, "output": "done", "redacted_account": "Аккаунт 1"}

    class _LosingQueue(_Queue):
        def __init__(self) -> None:
            super().__init__(LeaseRenewal(version=2, lease_expires_at=None))
            self.renewals = 0

        def renew_lease(self, **kwargs):
            self.renewals += 1
            if self.renewals == 1:
                return super().renew_lease(**kwargs)
            return None

    runtime = _SlowRuntime()
    worker._runtime = runtime
    worker._queue = _LosingQueue()
    await worker._process(_record())
    assert runtime.calls == 1
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


def _delivery(account: str) -> CommandRecord:
    return CommandRecord(
        id=uuid4(),
        request=CommandPayload(
            kind=CommandKind.BROADCAST,
            mode=CommandMode.DRY_RUN,
            account=account,
            payload=BroadcastPayload(text="wave"),
        ),
        requested_by=1,
        status=CommandStatus.CLAIMED,
        created_at=datetime.now(UTC),
        version=1,
    )


@pytest.mark.asyncio
async def test_lost_lease_kills_runtime_process():
    worker = ControlWorker(_settings())
    worker._lease_renew_interval = 0.01
    killed: list = []

    class _Killable(_Runtime):
        async def run_command(self, _record):
            await asyncio.sleep(0.08)
            self.calls += 1
            return {"ok": True, "output": "done", "redacted_account": "Аккаунт 1"}

        async def terminate_command(self, command_id):
            killed.append(command_id)
            return True

    class _LosingQueue(_Queue):
        def __init__(self) -> None:
            super().__init__(LeaseRenewal(version=2, lease_expires_at=None))
            self.renewals = 0

        def renew_lease(self, **kwargs):
            self.renewals += 1
            if self.renewals == 1:
                return super().renew_lease(**kwargs)
            return None

    worker._runtime = _Killable()
    worker._queue = _LosingQueue()
    record = _record()
    await worker._process(record)
    assert killed == [record.id]
    assert worker._queue.completed == []


@pytest.mark.asyncio
async def test_stop_cancels_and_terminates_target():
    worker = ControlWorker(_settings())
    target = uuid4()
    killed: list = []

    class _StopRuntime(_Runtime):
        async def terminate_command(self, command_id):
            killed.append(command_id)
            return True

    worker._runtime = _StopRuntime()
    worker._queue = _Queue(LeaseRenewal(version=2, lease_expires_at=None))
    record = CommandRecord(
        id=uuid4(),
        request=CommandPayload(
            kind=CommandKind.STOP,
            mode=CommandMode.READ,
            account="main",
            payload=StopPayload(command_id=target, account="main"),
        ),
        requested_by=1,
        status=CommandStatus.CLAIMED,
        created_at=datetime.now(UTC),
        version=1,
    )
    await worker._process(record)
    assert killed == [target]
    assert worker._queue.completed[0]["cancelled"] == target
    assert worker._queue.completed[1]["status"] == CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_tick_runs_distinct_slots_in_parallel():
    worker = ControlWorker(_settings())
    started: list[str] = []
    seen_two = asyncio.Event()
    release = asyncio.Event()

    class _ParallelRuntime(_Runtime):
        async def run_command(self, record):
            started.append(record.request.account)
            if len(started) >= 2:
                seen_two.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            self.calls += 1
            return {"ok": True, "output": "done", "redacted_account": "Аккаунт 1"}

    class _BatchQueue(_Queue):
        def claim_commands(self, **_kwargs):
            return [_delivery("main"), _delivery("work2")]

    worker._runtime = _ParallelRuntime()
    worker._queue = _BatchQueue(LeaseRenewal(version=2, lease_expires_at=None))
    tick = asyncio.create_task(worker._tick())
    await seen_two.wait()
    release.set()
    assert await tick == 2
    assert set(started) == {"main", "work2"}


@pytest.mark.asyncio
async def test_failed_queue_event_is_warned(caplog: pytest.LogCaptureFixture):
    worker = ControlWorker(_settings())
    runtime = _Runtime()
    queue = _Queue(LeaseRenewal(version=2, lease_expires_at=None))

    def fail_event(**_kwargs) -> bool:
        return False

    queue.append_event = fail_event  # type: ignore[method-assign]
    worker._queue = queue
    worker._runtime = runtime
    with caplog.at_level("WARNING", logger="control.worker"):
        await worker._process(_record())
    assert "не удалось записать событие" in caplog.text
