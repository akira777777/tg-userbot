"""Регрессионные тесты Фаз 0–3: control plane, воркер, CLI и ядро bot.py."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

import bot
import web
from control.account_alias import AccountAliasMapper
from control.cli_argv import build_control_cli
from control.config import ControlSettings
from control.controller_bot import start as tg_start
from control.controller_bot import status as tg_status
from control.executor import UserbotRuntime
from control.models import (
    BlacklistAddPayload,
    BroadcastPayload,
    CommandKind,
    CommandMode,
    CommandPayload,
    CommandRecord,
    CommandStatus,
    ReadPayload,
    StopPayload,
)
from control.queue import LeaseRenewal, QueueRepository
from control.worker import ControlWorker


def _settings(**overrides) -> ControlSettings:
    base = dict(
        bot_token="123456789:AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPPQQRR",
        mini_app_url="https://example.test/app",
        owner_telegram_ids=frozenset({42}),
        session_secret="super-secret-session-token-value-1234567890",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="sb_secret_super_sensitive_key_value",
    )
    base.update(overrides)
    return ControlSettings(**base)


# ---------------------------------------------------------------------------
# Фаза 1: maybe_single() -> None в supabase==2.34.1 + проверка owner_ids
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, handler, table_name: str):
        self._handler = handler
        self._table = table_name
        self._op = "select"
        self._payload = None
        self._maybe_single = False

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def execute(self):
        return self._handler(self._table, self._op, self._payload, self._maybe_single)


class _FakeSupabaseClient:
    def __init__(self, handler):
        self._handler = handler

    def table(self, name: str):
        return _FakeQuery(self._handler, name)


def test_queue_handles_none_from_maybe_single():
    """supabase==2.34.1 возвращает None из maybe_single().execute() при 0 строк."""
    inserted = []

    def handler(table, op, payload, maybe_single):
        if maybe_single:
            return None
        if table == "control_commands" and op == "insert":
            inserted.append(payload)
            return SimpleNamespace(data=[payload])
        if table == "control_command_events":
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[])

    repo = QueueRepository(_settings(), client=_FakeSupabaseClient(handler))
    rec = repo.enqueue(
        owner_telegram_id=42,
        kind="STATUS",
        mode="READ",
        account="main",
        payload={},
        idempotency_key=uuid4(),
    )
    assert rec is not None
    assert len(inserted) == 1
    assert repo.renew_lease(rec.id, "worker-1", version=1) is None
    assert repo.complete(rec.id, CommandStatus.SUCCEEDED) is False
    assert repo.get_command(rec.id, owner_telegram_id=42) is None


@pytest.mark.asyncio
async def test_controller_bot_enforces_owner_ids():
    replies = []

    class _Msg:
        async def reply_text(self, text, **kwargs):
            replies.append((text, kwargs))

    ctx = SimpleNamespace(bot_data={"owner_ids": frozenset({42}), "mini_app_url": "https://mini.test"})

    stranger = SimpleNamespace(effective_user=SimpleNamespace(id=999), effective_message=_Msg())
    await tg_start(stranger, ctx)
    await tg_status(stranger, ctx)
    assert len(replies) == 2
    assert replies[0][0] == "Доступ запрещён." and "reply_markup" not in replies[0][1]
    assert replies[1][0] == "Доступ запрещён."

    owner = SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_message=_Msg())
    await tg_start(owner, ctx)
    assert "reply_markup" in replies[2][1]


# ---------------------------------------------------------------------------
# Фаза 2: строгая связка kind/payload, неблокирующий STOP, EXPIRED для LIVE,
#         редакция секретов, защита от инъекции флагов argparse
# ---------------------------------------------------------------------------


def test_command_payload_strictly_binds_payload_type_to_kind():
    parsed = CommandPayload(kind=CommandKind.BROADCAST, mode=CommandMode.DRY_RUN, payload={})
    assert isinstance(parsed.payload, BroadcastPayload)

    with pytest.raises(ValidationError):
        CommandPayload(
            kind=CommandKind.BROADCAST,
            mode=CommandMode.DRY_RUN,
            payload=BlacklistAddPayload(value="@spammer"),
        )
    with pytest.raises(ValidationError):
        CommandPayload(
            kind=CommandKind.DMSEND,
            mode=CommandMode.DRY_RUN,
            payload={"value": "@spammer"},
        )


@pytest.mark.asyncio
async def test_worker_stop_runs_immediately_while_slot_is_busy():
    worker = ControlWorker(_settings())
    broadcast_started = asyncio.Event()
    broadcast_release = asyncio.Event()
    stop_executed = asyncio.Event()
    target_id = uuid4()

    broadcast_rec = CommandRecord(
        id=target_id,
        request=CommandPayload(
            kind=CommandKind.BROADCAST,
            mode=CommandMode.DRY_RUN,
            account="main",
            payload=BroadcastPayload(text="long"),
        ),
        requested_by=42,
        status=CommandStatus.CLAIMED,
        created_at=datetime.now(UTC),
        version=1,
    )
    stop_rec = CommandRecord(
        id=uuid4(),
        request=CommandPayload(
            kind=CommandKind.STOP,
            mode=CommandMode.LIVE,
            account="main",
            payload=StopPayload(command_id=target_id, account="main"),
        ),
        requested_by=42,
        status=CommandStatus.CLAIMED,
        created_at=datetime.now(UTC),
        version=1,
    )

    batches = [[broadcast_rec], [stop_rec]]

    class _Queue:
        def __init__(self):
            self.cancelled = []
            self.completed = []

        def claim_commands(self, **_kw):
            return batches.pop(0) if batches else []

        def renew_lease(self, **_kw):
            return LeaseRenewal(version=2, lease_expires_at=None)

        def cancel(self, cmd_id, _wid):
            self.cancelled.append(cmd_id)
            return True

        def complete(self, **kw):
            self.completed.append(kw)
            return True

        def fail(self, **kw):
            self.completed.append(kw)
            return True

        def append_event(self, **_kw):
            return True

    class _Runtime:
        async def run_command(self, rec):
            if rec.request.kind == CommandKind.BROADCAST:
                broadcast_started.set()
                await broadcast_release.wait()
                return {"ok": False, "error": "cancelled", "redacted_account": "Аккаунт 1"}
            stop_executed.set()
            return {"ok": True, "output": "stopped", "redacted_account": "Аккаунт 1"}

        async def terminate_command(self, cmd_id):
            if cmd_id == target_id:
                broadcast_release.set()
            return True

    worker._queue = _Queue()
    worker._runtime = _Runtime()

    assert await worker._tick() == 1
    await asyncio.wait_for(broadcast_started.wait(), timeout=2)
    # Второй тик не заблокирован: немедленно забирает и исполняет STOP
    assert await worker._tick() == 1
    await asyncio.wait_for(stop_executed.wait(), timeout=2)
    await worker.drain()
    assert target_id in worker._queue.cancelled


@pytest.mark.asyncio
async def test_worker_expires_reclaimed_live_command_instead_of_rerunning():
    worker = ControlWorker(_settings())
    called = {"n": 0}
    completed = []

    class _Queue:
        def renew_lease(self, **_kw):
            return LeaseRenewal(version=3, lease_expires_at=None)

        def complete(self, **kw):
            completed.append(kw)
            return True

        def append_event(self, **_kw):
            return True

    class _Runtime:
        async def run_command(self, _rec):
            called["n"] += 1
            return {"ok": True, "output": "sent", "redacted_account": "Аккаунт 1"}

    worker._queue = _Queue()
    worker._runtime = _Runtime()

    stale_live = CommandRecord(
        id=uuid4(),
        request=CommandPayload(
            kind=CommandKind.BROADCAST,
            mode=CommandMode.LIVE,
            account="main",
            payload=BroadcastPayload(text="live"),
            confirmation_nonce=uuid4(),
        ),
        requested_by=42,
        status=CommandStatus.CLAIMED,
        created_at=datetime.now(UTC),
        version=2,  # повторно забранная после истечения аренды
    )
    await worker._process(stale_live)
    assert called["n"] == 0
    assert len(completed) == 1
    assert completed[0]["status"] == CommandStatus.EXPIRED
    assert completed[0]["error_code"] == "expired"


def test_executor_redacts_windows_session_paths_and_secrets():
    settings = _settings()
    runtime = UserbotRuntime(settings, Path("."))
    raw = (
        r"Ошибка C:\Users\novra\tg-userbot\sessions\work2.session "
        "API_ID=123456 API_HASH=0123456789abcdef0123456789abcdef "
        f"BOT_TOKEN={settings.bot_token} {settings.session_secret}"
    )
    res = runtime._result(0, raw.encode("utf-8"), b"", "main")
    out = res["output"]
    assert "work2.session" not in out
    assert "123456" not in out
    assert "0123456789abcdef" not in out
    assert settings.bot_token not in out
    assert settings.session_secret not in out
    assert "<session_file>" in out


def test_cli_argv_prevents_flag_injection_and_separates_campaigns():
    spec = build_control_cli(
        CommandKind.BROADCAST,
        BroadcastPayload(text="--dry-run"),
        "main",
        CommandMode.LIVE,
    )
    assert spec is not None
    parsed = bot.build_parser().parse_args(spec.argv)
    assert parsed.text == "--dry-run"
    assert parsed.dry_run is False

    bl = build_control_cli(
        CommandKind.BLACKLIST_ADD,
        BlacklistAddPayload(value="-1001234567890", reason="--reason-like"),
        "main",
        CommandMode.LIVE,
    )
    assert bl is not None
    parsed_bl = bot.build_parser().parse_args(bl.argv)
    assert parsed_bl.value == "-1001234567890"
    assert parsed_bl.reason == "--reason-like"

    camps = build_control_cli(CommandKind.CAMPAIGNS, ReadPayload(), "main", CommandMode.READ)
    status = build_control_cli(CommandKind.STATUS, ReadPayload(), "main", CommandMode.READ)
    assert camps is not None and status is not None
    assert camps.argv == ["report"]
    assert status.argv == ["status"]


def test_account_alias_mapper_is_deterministic():
    m1 = AccountAliasMapper(["work2", "main", "acc3"])
    m2 = AccountAliasMapper(["acc3", "work2", "main"])
    assert [m1.mask_internal_name(n) for n in ("main", "acc3", "work2")] == [
        "Аккаунт 1",
        "Аккаунт 2",
        "Аккаунт 3",
    ]
    assert [m2.mask_internal_name(n) for n in ("main", "acc3", "work2")] == [
        "Аккаунт 1",
        "Аккаунт 2",
        "Аккаунт 3",
    ]


def test_control_cli_entrypoints_healthcheck():
    env = dict(os.environ)
    env.update(
        {
            "BOT_TOKEN": "123456789:testtoken",
            "MINI_APP_URL": "https://example.test",
            "OWNER_TELEGRAM_IDS": "42",
            "CONTROL_SESSION_SECRET": "a" * 40,
        }
    )
    for mod_args in (["-m", "control", "main", "healthcheck"], ["-m", "control.main", "healthcheck"]):
        proc = subprocess.run(
            [sys.executable, *mod_args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.startswith("OK ")


# ---------------------------------------------------------------------------
# Фаза 2: исправления ядра bot.py и web.py dm_available
# ---------------------------------------------------------------------------


def test_dmsend_from_source_dry_run_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "main.session"))
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "stop.json")
    conn = bot.connect()
    conn.execute(
        "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,deleted,access_hash) "
        "VALUES(101,'alice','Alice','',0,?,0,555)",
        (bot.now_iso(),),
    )
    conn.commit()
    conn.close()

    args = argparse.Namespace(
        account="main",
        text=None,
        file=None,
        media=None,
        from_source="@promo_channel",
        msg_id=[42],
        forward_from="raw",
        drop_author=True,
        no_preview=False,
        dry_run=True,
        limit=5,
        group=None,
        repeat_ok=False,
        delay_min=0,
        delay_max=0,
        max_runs=0,
        opt_out=True,
    )
    sent_ok = asyncio.run(bot.cmd_dmsend(args))
    assert sent_ok == 0


def test_forward_run_records_msg_id_from_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "main.session"))
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "stop.json")
    conn = bot.connect()
    try:
        fake_updates = SimpleNamespace(updates=[SimpleNamespace(message=SimpleNamespace(id=777))])

        class _Client:
            async def __call__(self, _req):
                return fake_updates

        args = argparse.Namespace(
            dry_run=False, delay_min=0, delay_max=0, max_runs=0,
            drop_author=True, forward_from="raw", no_preview=False,
        )
        asyncio.run(
            bot.forward_run(
                _Client(),
                conn,
                "broadcast",
                [{"id": 1, "title": "Chat"}],
                "src",
                [10],
                args,
                resolve=lambda r: ("Chat", "peer", "1", {}),
                account="main",
            )
        )
        row = conn.execute("SELECT msg_id FROM sent WHERE target='1'").fetchone()
        assert row["msg_id"] == 777
    finally:
        conn.close()


def test_endless_loop_interrupts_long_sleep_on_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    sleeps = []

    def fake_sleep(chunk):
        sleeps.append(chunk)
        # После первого короткого шага сна приходит сигнал stop.
        bot.request_stop("all")

    monkeypatch.setattr(bot.time, "sleep", fake_sleep)
    monkeypatch.setattr(bot, "loop_gap", lambda *_a, **_kw: 600.0)

    rounds = {"n": 0}

    async def run_once():
        rounds["n"] += 1
        return 1

    total = bot.run_endless(argparse.Namespace(loop_gap=600.0, loop_jitter=0.0), run_once)
    assert rounds["n"] == 1
    assert total == 1
    assert sleeps == [bot.LOOP_STOP_POLL]


def test_web_dm_available_uses_dm_eligible_where(tmp_path, monkeypatch):
    db_file = tmp_path / "userbot.db"
    monkeypatch.setattr(bot, "DB_PATH", db_file)
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "main.session"))
    monkeypatch.setattr(web, "DB", db_file)
    conn = bot.connect()
    now = bot.now_iso()
    conn.executemany(
        "INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,deleted,access_hash,dm_block) "
        "VALUES(?,?,?,'',?, ?,?,?,?)",
        [
            (1, "real_user", "Анна", 0, now, 0, 111, None),
            (2, "SomeBot", "Бот", 0, now, 0, 222, None),
            (3, "Kami072895", "Твинк", 0, now, 0, 333, None),
            (4, "blocked", "Блок", 0, now, 0, 444, "user_blocked"),
            (5, "deleted", "Удалён", 0, now, 1, 555, None),
            (6, "promo", "t.me/spam", 0, now, 0, 666, None),
        ],
    )
    conn.commit()
    conn.close()

    state = web.read_state()
    assert state["dm_available"] == 1
