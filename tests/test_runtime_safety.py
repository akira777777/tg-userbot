from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot
import web


def test_plaintext_account_secret_leaves_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "SECRETS_PATH", tmp_path / "account-secrets.json")
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    connection = bot.connect()
    try:
        connection.execute(
            "UPDATE accounts SET api_id='11', api_hash='secret-hash' WHERE name='main'"
        )
        connection.commit()
        row = connection.execute("SELECT * FROM accounts WHERE name='main'").fetchone()
        bot.migrate_plaintext_secret(connection, "main", row)
        assert bot.load_account_secrets()["main"]["api_hash"] == "secret-hash"
        cleared = connection.execute("SELECT api_id, api_hash FROM accounts WHERE name='main'").fetchone()
        assert cleared["api_id"] is None
        assert cleared["api_hash"] is None
    finally:
        connection.close()


def test_stop_request_matches_only_later_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    bot.request_stop("main")
    assert bot.stop_requested("main", "2000-01-01T00:00:00") is True
    assert bot.stop_requested("work2", "2000-01-01T00:00:00") is False


def test_healthcheck_creates_readable_sqlite(tmp_path):
    database = tmp_path / "userbot.db"
    completed = subprocess.run(
        [sys.executable, "scripts/healthcheck.py", "--db", str(database)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "OK"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_bot_parser_exposes_runtime_commands():
    parser = bot.build_parser()
    assert parser.parse_args(["stop", "--account", "main"]).func == bot.cmd_stop
    assert parser.parse_args(["blacklist", "add", "123"]).func == bot.cmd_blacklist


@pytest.mark.asyncio
async def test_local_panel_writes_without_token():
    async with TestClient(TestServer(web.build_app())) as client:
        response = await client.post("/api/run", json={"action": "not-a-command"})
        assert response.status == 400
        body = await response.json()
        assert "unauthorized" not in body.get("error", "")


@pytest.mark.asyncio
async def test_posix_stop_signals_process_group(monkeypatch):
    monkeypatch.setattr(web, "_windows", lambda: False)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(web.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(web, "_pid_alive", lambda _pid: True)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(web.asyncio, "sleep", no_sleep)
    assert await web._kill_tree(55) is True
    assert signals == [(55, signal.SIGTERM), (55, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_posix_stop_returns_false_when_process_is_gone(monkeypatch):
    monkeypatch.setattr(web, "_windows", lambda: False)

    def missing(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(web.os, "killpg", missing)
    assert await web._kill_tree(55) is False


@pytest.mark.asyncio
async def test_windows_stop_uses_taskkill(monkeypatch):
    monkeypatch.setattr(web, "_windows", lambda: True)
    seen: dict[str, tuple] = {}

    class _Proc:
        returncode = 0

        async def wait(self):
            return 0

    async def fake_exec(*cmd, **_kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(web.asyncio, "create_subprocess_exec", fake_exec)
    assert await web._kill_tree(4321) is True
    assert seen["cmd"][:4] == ("taskkill", "/PID", "4321", "/T")


@pytest.mark.asyncio
async def test_panel_job_starts_its_own_session_on_posix(monkeypatch):
    monkeypatch.setattr(web, "_windows", lambda: False)
    captured: dict[str, dict] = {}

    class _Stdout:
        async def readline(self):
            return b""

    class _Proc:
        pid = 77
        stdout = _Stdout()

        def kill(self):
            return None

        async def wait(self):
            return 0

    async def fake_exec(*_cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(web.asyncio, "create_subprocess_exec", fake_exec)
    web.BUS.jobs["contract-slot"] = {"name": "status", "kill": False}
    await web.run_job(["status"], "status", "main", "contract-slot")
    assert captured["kwargs"]["start_new_session"] is True
    assert "contract-slot" not in web.BUS.jobs
