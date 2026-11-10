"""Аргументы панели и воркера должны разбираться тем же парсером, что и CLI."""

from __future__ import annotations

from pathlib import Path

import bot
import web
from control.config import ControlSettings
from control.executor import UserbotRuntime
from control.models import (
    BlacklistAddPayload,
    BroadcastPayload,
    CommandKind,
    CommandMode,
    ConductPayload,
    DmSendPayload,
    ReadPayload,
    StopPayload,
)


def _runtime() -> UserbotRuntime:
    settings = ControlSettings(
        bot_token="token",
        mini_app_url="https://example.test/app",
        owner_telegram_ids=frozenset({1}),
    )
    return UserbotRuntime(settings, Path("."))


def _parse(argv: list[str]):
    try:
        parsed = bot.build_parser().parse_args(argv)
    except SystemExit as exc:
        raise AssertionError(f"bot.py не принял argv {argv}") from exc
    # blacklist объявляет свой positional «cmd» и затирает имя подкоманды.
    expected = "add" if argv[0] == "blacklist" else argv[0]
    assert parsed.cmd == expected, argv
    return parsed


def test_executor_argv_matches_bot_parser():
    runtime = _runtime()
    cases = [
        (CommandKind.STATUS, CommandMode.READ, ReadPayload(), "main"),
        (CommandKind.GROUPS, CommandMode.READ, ReadPayload(), "main"),
        (CommandKind.CAMPAIGNS, CommandMode.READ, ReadPayload(), "main"),
        (CommandKind.MEDIA_LIST, CommandMode.READ, ReadPayload(), "work1"),
        (CommandKind.BLACKLIST_ADD, CommandMode.DRY_RUN, BlacklistAddPayload(value="123"), "main"),
        (CommandKind.STOP, CommandMode.DRY_RUN, StopPayload(account="all"), "main"),
        (CommandKind.STOP, CommandMode.DRY_RUN, StopPayload(account="work2"), "main"),
        (CommandKind.BROADCAST, CommandMode.DRY_RUN, BroadcastPayload(text="hi", group_ids=[1]), "main"),
        (CommandKind.BROADCAST, CommandMode.LIVE, BroadcastPayload(text="live"), "main"),
        (CommandKind.DMSEND, CommandMode.DRY_RUN, DmSendPayload(text="hi", limit=5, group_id=3), "main"),
        (CommandKind.CONDUCT, CommandMode.DRY_RUN, ConductPayload(kind="dm", text="wave", group_id=9), "main"),
        (CommandKind.CONDUCT, CommandMode.DRY_RUN, ConductPayload(kind="broadcast", text="wave"), "all"),
    ]
    for kind, mode, payload, account in cases:
        argv = runtime._build_argv(kind, payload, account, mode)
        assert argv is not None, kind
        _parse(argv)


def test_web_panel_argv_matches_bot_parser():
    samples = [
        {"action": "status"},
        {"action": "sync", "account": "main"},
        {"action": "scrape", "account": "main", "ids": [1, 2]},
        {"action": "broadcast", "account": "main", "text": "hi", "dry": True, "ids": [4]},
        {"action": "dmsend", "account": "main", "text": "hi", "dry": True, "limit": 3, "group": 8},
        {"action": "conduct", "kind": "dm", "text": "hi", "dry": True, "accounts": ["main"]},
        {"action": "blacklist_add", "value": "@name"},
        {"action": "cloud", "op": "status"},
        {"action": "media", "op": "list"},
        {"action": "accounts", "op": "list"},
        {"action": "export"},
        {"action": "probe", "account": "main", "keep": True},
        {"action": "privacy", "account": "main"},
        {"action": "align", "account": "main", "dry": True, "limit": 2},
        {"action": "compare", "account": "main", "other": "work1"},
        {"action": "watch", "account": "main"},
    ]
    for payload in samples:
        argv, _slot, _account = web.build_args(payload)
        _parse(argv)
