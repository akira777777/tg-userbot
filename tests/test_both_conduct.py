"""Режим conduct both: сон до безопасного момента и границы очередей."""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="ubb_both_")
os.environ["USERBOT_DB"] = str(Path(_tmp) / "t.db")
os.environ["USERBOT_SESSIONS"] = str(Path(_tmp) / "sessions")

from bot import (  # noqa: E402
    DIALOG_REFRESH_SECONDS,
    assign_targets,
    both_should_continue,
    dialog_refresh_mode,
    earliest_moment,
    fanout,
    hold_dm_queue,
    next_channel,
    reassign_groups_on_flood,
)
from control.cli_argv import build_control_cli  # noqa: E402
from control.models import CommandKind, CommandMode, ConductPayload  # noqa: E402
import web  # noqa: E402


def test_earliest_moment_picks_flood_before_budget_and_group():
    now = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    flood = now + timedelta(hours=2)
    budget_reset = datetime(2026, 11, 11, tzinfo=timezone.utc)
    group_ready = now + timedelta(hours=8)
    assert earliest_moment(now, [flood, budget_reset, group_ready]) == flood
    assert earliest_moment(now, [budget_reset, group_ready]) == group_ready


def test_both_dry_run_does_not_loop():
    assert both_should_continue(dry_run=True, stopped=False) is False
    assert both_should_continue(dry_run=False, stopped=False) is True
    assert both_should_continue(dry_run=False, stopped=True) is False


def test_reassign_moves_only_writable_groups_within_budget():
    pending = [{"id": 1, "title": "shared"}, {"id": 2, "title": "only-main"}]
    players = {
        "main": {"budget": 10, "used": 10, "flood_until": None},
        "work2": {"budget": 1, "used": 0, "flood_until": None},
    }
    edges = {"main": {1, 2}, "work2": {1}}
    stay, moves = reassign_groups_on_flood("main", pending, players, edges)
    assert [row["id"] for _who, row in moves] == [1]
    assert [row["id"] for row in stay] == [2]
    assert players["work2"]["used"] == 1


def test_peerflood_holds_people_on_the_same_account():
    current = {"user_id": 7, "username": "a"}
    pending = [{"user_id": 8, "username": "b"}]
    held = hold_dm_queue(current, pending)
    assert [row["user_id"] for row in held] == [7, 8]
    assert held[0] is current


def test_channels_do_not_give_one_target_to_two_accounts():
    groups = [{"id": 1, "title": "only-main"}, {"id": 2, "title": "shared"}]
    people = [{"user_id": 10, "username": "u"}, {"user_id": 11, "username": "v"}]
    players = {
        "main": {"budget": 15, "used": 0, "flood_until": None},
        "work2": {"budget": 15, "used": 0, "flood_until": None},
    }
    edges = {"main": {1, 2}, "work2": {2}}
    group_plan, group_gap = assign_targets("broadcast", groups, players, edges)
    dm_plan, dm_gap = assign_targets("dm", people, dict(players), None)
    assert group_gap == 0 and dm_gap == 0
    assert len({row["id"] for _who, row in group_plan}) == len(group_plan)
    assert len({row["user_id"] for _who, row in dm_plan}) == len(dm_plan)
    assert {who for who, row in group_plan if row["id"] == 1} == {"main"}


def test_dm_waits_twenty_seconds_after_a_group():
    assert next_channel(True, True, since_any=5, last_kind="broadcast") == "broadcast"
    assert next_channel(True, True, since_any=20, last_kind="broadcast") == "dm"
    assert next_channel(False, True, since_any=5, last_kind="broadcast") is None


def test_both_argv_has_no_time_window():
    spec = build_control_cli(
        CommandKind.CONDUCT,
        ConductPayload(kind="both", text="проверка", window_hours=6),
        "all",
        CommandMode.DRY_RUN,
    )
    assert spec is not None
    assert spec.argv[spec.argv.index("--what") + 1] == "both"
    assert "--window" not in spec.argv
    argv, slot, _account = web.build_args(
        {"action": "conduct", "kind": "both", "text": "проверка", "dry": True, "window": 6}
    )
    assert slot == "conductor"
    assert "--window" not in argv
    assert "both" in argv


def test_fanout_runs_several_accounts_together():
    current = 0
    peak = 0

    async def worker(name):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.05)
        current -= 1
        return name

    async def main():
        return await fanout(["a", "b", "c", "d"], worker, limit=3)

    assert asyncio.run(main()) == ["a", "b", "c", "d"]
    assert peak == 3


def test_dialog_refresh_waits_once_then_goes_to_background():
    assert dialog_refresh_mode(False, False, 0) == "wait"
    assert dialog_refresh_mode(True, True, DIALOG_REFRESH_SECONDS) == "wait"
    assert dialog_refresh_mode(True, False, DIALOG_REFRESH_SECONDS - 1) == "skip"
    assert dialog_refresh_mode(True, False, DIALOG_REFRESH_SECONDS, inflight=True) == "skip"
    assert dialog_refresh_mode(True, False, DIALOG_REFRESH_SECONDS) == "background"


def test_both_dry_run_argv_keeps_kind_and_never_repeats():
    spec = build_control_cli(
        CommandKind.CONDUCT,
        ConductPayload(kind="both", text="проверка", limit=5, repeat_ok=False),
        "all",
        CommandMode.DRY_RUN,
    )
    assert spec is not None
    assert spec.argv[:3] == ["conduct", "--what", "both"]
    assert "--repeat-ok" not in spec.argv
    assert "--dry-run" in spec.argv
