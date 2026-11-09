from __future__ import annotations

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
async def test_local_panel_writes_fail_closed_without_token(monkeypatch):
    monkeypatch.delenv("WEB_TOKEN", raising=False)
    async with TestClient(TestServer(web.build_app())) as client:
        response = await client.post("/api/run", json={"action": "status"})
        assert response.status == 503

    monkeypatch.setenv("WEB_TOKEN", "0123456789abcdef")
    async with TestClient(TestServer(web.build_app())) as client:
        denied = await client.post("/api/run", json={"action": "status"})
        assert denied.status == 401
        accepted = await client.post(
            "/api/run",
            json={"action": "not-a-command"},
            headers={"X-Web-Token": "0123456789abcdef"},
        )
        assert accepted.status != 401
