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
        CommandKind.STATUS: ["status", "--account", "main"],
        CommandKind.GROUPS: ["groups", "--list", "--account", "main"],
        CommandKind.MEDIA_LIST: ["media", "list", "--account", "main"],
    }
    for kind, argv in expected.items():
        built = runtime._build_argv(kind, ReadPayload(), "main")
        assert built == argv
        assert "--dry-run" not in built


def test_delivery_commands_stay_dry_run_even_when_live():
    runtime = _runtime()
    broadcast = runtime._build_argv(
        CommandKind.BROADCAST,
        BroadcastPayload(text="привет", group_ids=[3]),
        "main",
    )
    direct = runtime._build_argv(
        CommandKind.DMSEND,
        DmSendPayload(text="личка", limit=5),
        "main",
    )
    conduct = runtime._build_argv(
        CommandKind.CONDUCT,
        ConductPayload(kind="dm", text="волна", group_id=9),
        "main",
    )
    live = _record(CommandKind.BROADCAST, CommandMode.LIVE, BroadcastPayload(text="live"))
    live_argv = runtime._build_argv(live.request.kind, live.request.payload, live.request.account)
    for argv in (broadcast, direct, conduct, live_argv):
        assert argv is not None
        assert argv[-1] == "--dry-run"


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
