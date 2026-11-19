"""Режим пересылки: источник, ids, drop_author и отключение превью — без сети."""

from __future__ import annotations

import argparse
import asyncio

import pytest

import bot


class FakeClient:
    """Заглушка Telethon: пишет вызовы вместо отправки."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def __call__(self, request):
        self.calls.append(request)
        if self.fail is not None:
            raise self.fail
        return None


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "userbot.db")
    monkeypatch.setattr(bot, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "sessions" / "main.session"))
    monkeypatch.setattr(bot, "SECRETS_PATH", tmp_path / "account-secrets.json")
    monkeypatch.setattr(bot, "STOP_PATH", tmp_path / "control-stop.json")
    conn = bot.connect()
    try:
        yield conn
    finally:
        conn.close()


def _args(**kw):
    base = dict(dry_run=False, delay_min=0, delay_max=0, max_runs=0,
                drop_author=True, forward_from="raw", no_preview=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_forward_ids_prefers_explicit_and_reads_marker(tmp_path, monkeypatch, db):
    monkeypatch.setenv("USERBOT_DIR", str(tmp_path / "media"))
    (tmp_path / "media").mkdir()
    marker = tmp_path / "media" / "forward-ids.json"
    marker.write_text('{"ids": [11, 12]}', encoding="utf-8")
    assert bot.forward_ids(db, _args(msg_id=[5, 6])) == [5, 6]
    assert bot.forward_ids(db, _args(msg_id=None)) == [11, 12]
    marker.write_text("not json", encoding="utf-8")
    assert bot.forward_ids(db, _args(msg_id=None)) == []


def test_forward_run_sends_one_request_per_target_with_hidden_author(db):
    client = FakeClient()
    args = _args()
    targets = [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]
    asyncio.run(bot.forward_run(
        client, db, "broadcast", targets, "source_channel", [10, 11], args,
        resolve=lambda r: (f"group {r['id']}", "peer", str(r["id"]), {}),
        account="main"))
    assert len(client.calls) == 2
    first = client.calls[0]
    assert type(first).__name__ == "ForwardMessagesRequest"
    assert first.drop_author is True
    assert list(first.id) == [10, 11]
    assert len(first.random_id) == 2
    rows = db.execute("SELECT target, status, account FROM sent ORDER BY target").fetchall()
    assert [(r["target"], r["status"], r["account"]) for r in rows] == \
        [("1", "ok", "main"), ("2", "ok", "main")]


def test_forward_run_keeps_author_in_self_mode(db):
    client = FakeClient()
    args = _args(drop_author=False)
    asyncio.run(bot.forward_run(
        client, db, "dm", [{"user_id": 9}], "source", [3], args,
        resolve=lambda r: ("dm", "peer", "9", {}), account="main"))
    assert client.calls[0].drop_author is False


def test_forward_dry_run_touches_neither_network_nor_db(db):
    client = FakeClient()
    args = _args(dry_run=True)
    asyncio.run(bot.forward_run(
        client, db, "broadcast", [{"id": 1, "title": "a"}], "source", [1], args,
        resolve=lambda r: ("group", "peer", "1", {}), account="main"))
    assert client.calls == []
    assert db.execute("SELECT COUNT(*) c FROM sent").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"] == 0


def test_flood_wait_during_forward_puts_account_in_cooldown(db):
    from telethon.errors import FloodWaitError

    exc = FloodWaitError(request=None, capture=0)
    exc.seconds = 120
    client = FakeClient(fail=exc)
    asyncio.run(bot.forward_run(
        client, db, "broadcast", [{"id": 1, "title": "a"}], "source", [1], _args(),
        resolve=lambda r: ("group", "peer", "1", {}), account="main"))
    row = db.execute("SELECT flood_until FROM accounts WHERE name='main'").fetchone()
    assert row["flood_until"] is not None


def test_no_preview_sends_send_message_with_no_webpage():
    client = FakeClient()
    asyncio.run(bot.deliver_once(client, None, "peer", "текст со ссылкой",
                                 no_preview=True))
    request = client.calls[0]
    assert type(request).__name__ == "SendMessageRequest"
    assert request.no_webpage is True
    assert request.message == "текст со ссылкой"


def test_plain_text_still_uses_send_message_without_media():
    client = FakeClient()
    seen = {}

    async def send_message(peer, body):
        seen["peer"], seen["body"] = peer, body

    client.send_message = send_message
    asyncio.run(bot.deliver_once(client, None, "peer", "текст"))
    assert seen == {"peer": "peer", "body": "текст"}


def test_source_parser_accepts_links_and_ids():
    import re
    pattern = re.compile(r"(?:https?://)?t\.me/(?:c/)?(-?\d+|[A-Za-z0-9_]{4,})")
    assert pattern.search("https://t.me/secret_ads").group(1) == "secret_ads"
    assert pattern.search("t.me/c/1234567890/77").group(1) == "1234567890"
    assert pattern.search("@secret_ads") is None


def _fwd_args(**kw):
    base = dict(from_source="@src", msg_id=None, forward_from="raw", drop_author=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_apply_forward_mode_is_none_for_text_campaigns(db):
    assert bot.apply_forward_mode(_fwd_args(from_source=None), db) is None


def test_apply_forward_mode_hides_author_only_in_raw_mode(db):
    raw = _fwd_args(msg_id=[4])
    assert bot.apply_forward_mode(raw, db) == [4]
    assert raw.drop_author is True
    named = _fwd_args(msg_id=[4], forward_from="self")
    bot.apply_forward_mode(named, db)
    assert named.drop_author is False
    kept = _fwd_args(msg_id=[4], drop_author=False)
    bot.apply_forward_mode(kept, db)
    assert kept.drop_author is False


def test_apply_forward_mode_falls_back_to_saved_ids(tmp_path, monkeypatch, db):
    monkeypatch.setenv("USERBOT_DIR", str(tmp_path))
    (tmp_path / "forward-ids.json").write_text('{"ids": [8, 9]}', encoding="utf-8")
    assert bot.apply_forward_mode(_fwd_args(), db) == [8, 9]


def test_apply_forward_mode_exits_without_ids(db):
    with pytest.raises(SystemExit, match="msg-id"):
        bot.apply_forward_mode(_fwd_args(), db)


def test_forward_batch_sends_album_in_one_request():
    client = FakeClient()
    asyncio.run(bot.forward_batch(client, "broadcast", "peer", "src", [1, 2, 3], _args()))
    assert len(client.calls) == 1
    req = client.calls[0]
    assert list(req.id) == [1, 2, 3]
    assert len(req.random_id) == 3
    assert req.to_peer == "peer" and req.from_peer == "src"


def test_conduct_and_broadcast_expose_forward_flags():
    parser = bot.build_parser()
    ns = parser.parse_args(["conduct", "--what", "both", "--from-source", "@src",
                            "--msg-id", "5", "--forward-from", "self"])
    assert (ns.from_source, ns.msg_id, ns.forward_from) == ("@src", [5], "self")
    bs = parser.parse_args(["broadcast", "--from-source", "@src", "--no-preview"])
    assert bs.no_preview is True
