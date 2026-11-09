from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from control.config import ControlSettings
from control.models import (
    CommandKind,
    CommandMode,
    CommandPayload,
    CommandRecord,
    CommandStatus,
    ReadPayload,
)
from control.worker import ControlWorker


class FakeQueue:
    def __init__(self, claimed: list[CommandRecord] | None = None) -> None:
        self.claimed = claimed or []
        self.renewed: list[tuple[object, str, int, int]] = []
        self.completed: list[tuple[object, CommandStatus, str]] = []
        self.failed: list[tuple[object, str, str]] = []
        self.events: list[dict[str, Any]] = []

    def claim_commands(self, worker_id: str, batch_size: int, lease_seconds: int) -> list[CommandRecord]:
        assert worker_id
        assert batch_size > 0
        assert lease_seconds > 0
        return list(self.claimed)

    def renew_lease(self, command_id: object, worker_id: str, version: int, lease_seconds: int) -> None:
        self.renewed.append((command_id, worker_id, version, lease_seconds))

    def complete(self, command_id: object, status: CommandStatus, result_summary: str) -> None:
        self.completed.append((command_id, status, result_summary))

    def fail(self, command_id: object, error_code: str, result_summary: str) -> None:
        self.failed.append((command_id, error_code, result_summary))

    def append_event(
        self,
        command_id: object,
        sequence: int,
        level: str,
        event_type: str,
        safe_message: str,
    ) -> None:
        self.events.append(
            {
                "command_id": command_id,
                "sequence": sequence,
                "level": level,
                "event_type": event_type,
                "safe_message": safe_message,
            }
        )


class FakeRuntime:
    def __init__(
        self, results: dict[object, dict[str, Any]] | None = None, default: dict[str, Any] | None = None
    ) -> None:
        self.results = results or {}
        self.default = default or {"ok": True, "output": "ok", "redacted_account": "Аккаунт 1"}
        self.seen: list[CommandRecord] = []

    async def run_command(self, record: CommandRecord) -> dict[str, Any]:
        self.seen.append(record)
        return self.results.get(record.id, self.default)


def _record() -> CommandRecord:
    return CommandRecord(
        id=uuid4(),
        request=CommandPayload(
            kind=CommandKind.STATUS,
            mode=CommandMode.READ,
            account="main",
            payload=ReadPayload(),
        ),
        requested_by=123,
        status=CommandStatus.CLAIMED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        version=3,
    )


def _worker(settings: ControlSettings, queue: FakeQueue, runtime: FakeRuntime) -> ControlWorker:
    worker = ControlWorker(settings)
    worker._queue = queue  # type: ignore[method-assign]
    worker._runtime = runtime  # type: ignore[method-assign]
    return worker


def test_shutdown_stops_loop_flag(settings: ControlSettings) -> None:
    worker = ControlWorker(settings)
    worker._running = True
    worker._shutdown()
    assert worker._running is False


@pytest.mark.asyncio
async def test_tick_noop_when_queue_empty(settings: ControlSettings) -> None:
    queue = FakeQueue([])
    runtime = FakeRuntime()
    worker = _worker(settings, queue, runtime)
    worker._running = True
    await worker._tick()
    assert runtime.seen == []
    assert queue.renewed == []


@pytest.mark.asyncio
async def test_tick_completes_successful_command(settings: ControlSettings) -> None:
    record = _record()
    queue = FakeQueue([record])
    runtime = FakeRuntime()
    worker = _worker(settings, queue, runtime)
    worker._running = True
    await worker._tick()
    assert runtime.seen == [record]
    assert queue.renewed[0][0] == record.id
    assert queue.completed[0][1] == CommandStatus.SUCCEEDED
    assert queue.events[0]["event_type"] == "completed"


@pytest.mark.asyncio
async def test_tick_fails_unsuccessful_command(settings: ControlSettings) -> None:
    record = _record()
    queue = FakeQueue([record])
    runtime = FakeRuntime(default={"ok": False, "error": "disk-full", "redacted_account": "Аккаунт 1"})
    worker = _worker(settings, queue, runtime)
    worker._running = True
    await worker._tick()
    assert queue.failed[0][1] == "disk-full"
    assert queue.events[0]["level"] == "error"


@pytest.mark.asyncio
async def test_tick_stops_when_shutdown_during_batch(settings: ControlSettings) -> None:
    first, second = _record(), _record()
    queue = FakeQueue([first, second])
    runtime = FakeRuntime()
    worker = _worker(settings, queue, runtime)
    worker._running = True

    original = runtime.run_command

    async def stop_after_first(record: CommandRecord) -> dict[str, Any]:
        result = await original(record)
        worker._shutdown()
        return result

    runtime.run_command = stop_after_first  # type: ignore[method-assign]
    await worker._tick()
    assert [item.id for item in runtime.seen] == [first.id]
