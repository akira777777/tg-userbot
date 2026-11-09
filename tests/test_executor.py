from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from control.config import ControlSettings
from control.executor import UserbotRuntime
from control.models import (
    BroadcastPayload,
    CommandKind,
    CommandMode,
    CommandPayload,
    CommandRecord,
    CommandStatus,
    ConductPayload,
    DmSendPayload,
    ReadPayload,
    StopPayload,
)


def _runtime(settings: ControlSettings) -> UserbotRuntime:
    return UserbotRuntime(settings, Path("/tmp/tg-userbot-project"))


def test_build_argv_read_only_kinds(settings: ControlSettings) -> None:
    runtime = _runtime(settings)
    payload = ReadPayload()
    assert runtime._build_argv(CommandKind.STATUS, payload, "main") == ["status", "--account", "main"]
    assert runtime._build_argv(CommandKind.GROUPS, payload, "main") == ["groups", "--list", "--account", "main"]
    assert runtime._build_argv(CommandKind.CAMPAIGNS, payload, "work1") == ["status", "--account", "work1"]
    assert runtime._build_argv(CommandKind.MEDIA_LIST, payload, "main") == ["media", "list", "--account", "main"]


def test_build_argv_broadcast_optional_flags(settings: ControlSettings) -> None:
    runtime = _runtime(settings)
    payload = BroadcastPayload(
        text="hello",
        media_id=9,
        group_ids=[1, 2],
        delay_min=1.5,
        delay_max=3.0,
        max_runs=4,
    )
    argv = runtime._build_argv(CommandKind.BROADCAST, payload, "main")
    assert argv is not None
    assert argv[:2] == ["broadcast", "--account"]
    assert "--dry-run" in argv
    assert argv[argv.index("--text") + 1] == "hello"
    assert argv[argv.index("--media") + 1] == "9"
    assert argv[argv.index("--ids") + 1 : argv.index("--ids") + 3] == ["1", "2"]
    assert argv[argv.index("--delay-min") + 1] == "1.5"
    assert argv[argv.index("--max-runs") + 1] == "4"


def test_build_argv_dmsend_and_conduct(settings: ControlSettings) -> None:
    runtime = _runtime(settings)
    dm = DmSendPayload(
        text="dm",
        media_id=2,
        group_id=44,
        limit=7,
        delay_min=0.2,
        delay_max=1.0,
        repeat_ok=True,
    )
    dm_argv = runtime._build_argv(CommandKind.DMSEND, dm, "main")
    assert dm_argv is not None
    assert "--repeat-ok" in dm_argv
    assert "--dry-run" in dm_argv
    assert dm_argv[dm_argv.index("--limit") + 1] == "7"

    conduct = ConductPayload(
        kind="broadcast",
        text="c",
        media_id=3,
        group_ids=[8],
        group_id=9,
        limit=5,
        window_hours=2.0,
        repeat_ok=True,
    )
    c_argv = runtime._build_argv(CommandKind.CONDUCT, conduct, "acc1")
    assert c_argv is not None
    assert c_argv[:4] == ["conduct", "--what", "broadcast", "--account"]
    assert "--dry-run" in c_argv
    assert c_argv[c_argv.index("--window") + 1] == "2.0"


def test_build_argv_rejects_mismatched_payload_and_stop(settings: ControlSettings) -> None:
    runtime = _runtime(settings)
    assert runtime._build_argv(CommandKind.BROADCAST, ReadPayload(), "main") is None
    assert runtime._build_argv(CommandKind.DMSEND, ReadPayload(), "main") is None
    assert runtime._build_argv(CommandKind.CONDUCT, ReadPayload(), "main") is None
    assert runtime._build_argv(CommandKind.STOP, StopPayload(), "main") is None
    assert runtime._build_argv(CommandKind.BLACKLIST_ADD, ReadPayload(), "main") is None


def _record(kind: CommandKind, payload: ReadPayload | BroadcastPayload) -> CommandRecord:
    return CommandRecord(
        id=uuid4(),
        request=CommandPayload(kind=kind, mode=CommandMode.READ, account="main", payload=payload),
        requested_by=123,
        status=CommandStatus.CLAIMED,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_run_command_unsupported_kind(settings: ControlSettings) -> None:
    runtime = _runtime(settings)
    result = await runtime.run_command(_record(CommandKind.STOP, ReadPayload()))
    assert result["ok"] is False
    assert "неподдерживаемая команда" in result["error"]


@pytest.mark.asyncio
async def test_run_command_subprocess_success(settings: ControlSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(settings)

    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"done", b""

    async def fake_exec(*_args: object, **_kwargs: object) -> FakeProc:
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await runtime.run_command(_record(CommandKind.STATUS, ReadPayload()))
    assert result["ok"] is True
    assert result["output"] == "done"


@pytest.mark.asyncio
async def test_run_command_subprocess_failure(settings: ControlSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(settings)

    class FakeProc:
        returncode = 7

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"boom"

    async def fake_exec(*_args: object, **_kwargs: object) -> FakeProc:
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await runtime.run_command(_record(CommandKind.STATUS, ReadPayload()))
    assert result["ok"] is False
    assert result["error"] == "boom"


@pytest.mark.asyncio
async def test_run_command_subprocess_exception(settings: ControlSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(settings)

    async def fake_exec(*_args: object, **_kwargs: object) -> object:
        raise OSError("cannot start")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await runtime.run_command(_record(CommandKind.STATUS, ReadPayload()))
    assert result["ok"] is False
    assert "cannot start" in result["error"]
