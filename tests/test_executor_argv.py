"""Аргументы bot.py из контрольной плоскости остаются read-only или dry-run."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

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
)


def _runtime() -> UserbotRuntime:
    settings = ControlSettings(
        bot_token="token",
        mini_app_url="https://example.test/app",
        owner_telegram_ids=frozenset({1}),
    )
    return UserbotRuntime(settings, Path("."))


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
