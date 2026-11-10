"""Аргументы bot.py из контрольной плоскости остаются read-only или dry-run."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from control.cli_argv import build_control_cli, slot_key_for_kind
from control.config import ControlSettings
from control.executor import UserbotRuntime
from control.models import (
    BlacklistAddPayload,
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
from control.slots import SlotLocker


def _runtime(tmp_path: Path | None = None) -> UserbotRuntime:
    settings = ControlSettings(
        bot_token="token",
        mini_app_url="https://example.test/app",
        owner_telegram_ids=frozenset({1}),
    )
    root = tmp_path or Path(".")
    locker = SlotLocker((tmp_path or Path(".")) / "locks") if tmp_path else None
    return UserbotRuntime(settings, root, locker=locker)


def _record(kind: CommandKind, mode: CommandMode, payload, account: str = "main") -> CommandRecord:
    nonce = uuid4() if mode == CommandMode.LIVE else None
    return CommandRecord(
        id=uuid4(),
        request=CommandPayload(
            kind=kind,
            mode=mode,
            account=account,
            payload=payload,
            confirmation_nonce=nonce,
        ),
        requested_by=1,
        status=CommandStatus.CLAIMED,
        created_at=datetime.now(UTC),
    )


def test_read_only_commands_do_not_send():
    runtime = _runtime()
    expected = {
        CommandKind.STATUS: ["status"],
        CommandKind.CAMPAIGNS: ["status"],
        CommandKind.GROUPS: ["groups", "--list", "--account", "main"],
        CommandKind.MEDIA_LIST: ["media", "list", "--account", "main"],
    }
    for kind, argv in expected.items():
        built = runtime._build_argv(kind, ReadPayload(), "main")
        assert built == argv
        assert "--dry-run" not in built


def test_blacklist_add_is_supported():
    argv = _runtime()._build_argv(
        CommandKind.BLACKLIST_ADD,
        BlacklistAddPayload(value="123"),
        "main",
        CommandMode.DRY_RUN,
    )
    assert argv == ["blacklist", "add", "123", "--reason", "вручную"]


def test_broadcast_repeat_ok_reaches_cli():
    runtime = _runtime()
    repeated = runtime._build_argv(
        CommandKind.BROADCAST,
        BroadcastPayload(text="ещё раз", repeat_ok=True),
        "main",
        CommandMode.DRY_RUN,
    )
    skipped = runtime._build_argv(
        CommandKind.BROADCAST,
        BroadcastPayload(text="ещё раз"),
        "main",
        CommandMode.DRY_RUN,
    )
    assert repeated is not None and "--repeat-ok" in repeated
    assert skipped is not None and "--repeat-ok" not in skipped


def test_delivery_mode_controls_dry_run():
    runtime = _runtime()
    broadcast = runtime._build_argv(
        CommandKind.BROADCAST,
        BroadcastPayload(text="привет", group_ids=[3]),
        "main",
        CommandMode.DRY_RUN,
    )
    direct = runtime._build_argv(
        CommandKind.DMSEND,
        DmSendPayload(text="личка", limit=5),
        "main",
        CommandMode.DRY_RUN,
    )
    conduct = runtime._build_argv(
        CommandKind.CONDUCT,
        ConductPayload(kind="dm", text="волна", group_id=9),
        "main",
        CommandMode.DRY_RUN,
    )
    live = _record(CommandKind.BROADCAST, CommandMode.LIVE, BroadcastPayload(text="live"))
    live_argv = runtime._build_argv(
        live.request.kind, live.request.payload, live.request.account, live.request.mode
    )
    for argv in (broadcast, direct, conduct):
        assert argv is not None
        assert argv[-1] == "--dry-run"
    assert live_argv is not None
    assert "--dry-run" not in live_argv
    assert conduct is not None and "--accounts" in conduct and "main" in conduct


def test_conduct_targets_named_account_only():
    runtime = _runtime()
    pinned = runtime._build_argv(
        CommandKind.CONDUCT,
        ConductPayload(kind="broadcast", text="всем"),
        "work2",
        CommandMode.DRY_RUN,
    )
    everyone = runtime._build_argv(
        CommandKind.CONDUCT,
        ConductPayload(kind="broadcast", text="всем"),
        "all",
        CommandMode.DRY_RUN,
    )
    assert pinned is not None
    assert pinned[pinned.index("--accounts") + 1] == "work2"
    assert everyone is not None
    assert "--accounts" not in everyone


@pytest.mark.asyncio
async def test_run_command_redacts_output_and_account_name(monkeypatch: pytest.MonkeyPatch):
    runtime = _runtime()

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"user 1234567890 session work2", b""

    async def fake_exec(*_cmd, **_kwargs):
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await runtime.run_command(_record(CommandKind.STATUS, CommandMode.READ, ReadPayload(), "work2"))
    assert result["ok"] is True
    assert result["redacted_account"] == "Аккаунт 1"
    assert "work2" not in result["redacted_account"]
    assert "1234567890" not in result["output"]
    assert "<user_id>" in result["output"]


def test_shared_cli_slot_keys():
    status = build_control_cli(CommandKind.STATUS, ReadPayload(), "main", CommandMode.READ)
    groups = build_control_cli(CommandKind.GROUPS, ReadPayload(), "work2", CommandMode.READ)
    dry = build_control_cli(
        CommandKind.BROADCAST, BroadcastPayload(text="hi"), "main", CommandMode.DRY_RUN
    )
    live = build_control_cli(
        CommandKind.BROADCAST, BroadcastPayload(text="hi"), "main", CommandMode.LIVE
    )
    assert status is not None and status.slot_key == "db" and "--dry-run" not in status.argv
    assert groups is not None and groups.slot_key == "db"
    assert dry is not None and dry.slot_key == "main" and dry.argv[-1] == "--dry-run"
    assert live is not None and "--dry-run" not in live.argv
    assert slot_key_for_kind(CommandKind.CONDUCT, "main") == "conductor"


class _HoldProc:
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self.returncode: int | None = None
        self._started = started
        self._release = release

    async def communicate(self):
        self._started.set()
        await self._release.wait()
        if self.returncode is None:
            self.returncode = 0
        return b"ok", b""

    def terminate(self) -> None:
        self.returncode = 1
        self._release.set()

    def kill(self) -> None:
        self.terminate()

    async def wait(self):
        await self._release.wait()


@pytest.mark.asyncio
async def test_same_account_slot_rejects_second_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = _runtime(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_exec(*_cmd, **_kwargs):
        return _HoldProc(started, release)

    monkeypatch.setattr("control.executor.asyncio.create_subprocess_exec", fake_exec)
    first = asyncio.create_task(
        runtime.run_command(
            _record(CommandKind.BROADCAST, CommandMode.DRY_RUN, BroadcastPayload(text="a"), "main")
        )
    )
    await started.wait()
    second = await runtime.run_command(
        _record(CommandKind.BROADCAST, CommandMode.DRY_RUN, BroadcastPayload(text="b"), "main")
    )
    assert second["ok"] is False
    assert second["error"] == "busy"
    release.set()
    assert (await first)["ok"] is True


@pytest.mark.asyncio
async def test_conduct_holds_the_account_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = _runtime(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_exec(*_cmd, **_kwargs):
        return _HoldProc(started, release)

    monkeypatch.setattr("control.executor.asyncio.create_subprocess_exec", fake_exec)
    first = asyncio.create_task(
        runtime.run_command(
            _record(
                CommandKind.CONDUCT,
                CommandMode.DRY_RUN,
                ConductPayload(kind="both", text="проверка"),
                "main",
            )
        )
    )
    await started.wait()
    second = await runtime.run_command(
        _record(CommandKind.BROADCAST, CommandMode.DRY_RUN, BroadcastPayload(text="b"), "main")
    )
    assert second["ok"] is False
    assert second["error"] == "busy"
    release.set()
    assert (await first)["ok"] is True


@pytest.mark.asyncio
async def test_stop_terminates_in_flight_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = _runtime(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_exec(*_cmd, **_kwargs):
        return _HoldProc(started, release)

    monkeypatch.setattr("control.executor.asyncio.create_subprocess_exec", fake_exec)
    record = _record(
        CommandKind.BROADCAST, CommandMode.DRY_RUN, BroadcastPayload(text="long"), "main"
    )
    task = asyncio.create_task(runtime.run_command(record))
    await started.wait()
    assert await runtime.terminate_command(record.id) is True
    result = await task
    assert result["ok"] is False
    stop_argv = runtime._build_argv(CommandKind.STOP, StopPayload(account="main"), "main")
    assert stop_argv == ["stop", "--account", "main"]
