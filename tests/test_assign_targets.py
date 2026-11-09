import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="ubb_assign_")
os.environ["USERBOT_DB"] = str(Path(_tmp) / "t.db")
os.environ["USERBOT_SESSIONS"] = str(Path(_tmp) / "sessions")

from bot import assign_targets, handoff_broadcast  # noqa: E402


def test_broadcast_exclusive_then_shared_budget():
    rows = [{"id": 1, "title": "only-main"}, {"id": 2, "title": "only-work2"}, {"id": 3, "title": "both"}]
    players = {
        "main": {"budget": 10, "used": 8, "flood_until": None},
        "work2": {"budget": 10, "used": 0, "flood_until": None},
    }
    edges = {"main": {1, 3}, "work2": {2, 3}}
    plan, unplanned = assign_targets("broadcast", rows, players, edges)
    by = {row["id"]: who for who, row in plan}
    assert by[1] == "main"
    assert by[2] == "work2"
    assert by[3] == "work2"
    assert unplanned == 0


def test_broadcast_nobody_without_edge():
    rows = [{"id": 9, "title": "ghost"}]
    players = {"main": {"budget": 5, "used": 0, "flood_until": None}}
    plan, unplanned = assign_targets("broadcast", rows, players, {"main": set()})
    assert plan[0][0] is None and unplanned == 1


def test_handoff_stops_when_both_accounts_drop_the_chat():
    row = {"id": 5, "title": "g"}
    players = {
        "main": {"budget": 10, "used": 0},
        "work2": {"budget": 10, "used": 0},
    }
    edges = {"main": {5}, "work2": {5}}
    edges["main"].discard(5)
    assert handoff_broadcast(row, "main", players, edges) == "work2"
    edges["work2"].discard(5)
    assert handoff_broadcast(row, "work2", players, edges) is None


def test_handoff_skips_account_without_budget():
    row = {"id": 5, "title": "g"}
    players = {
        "main": {"budget": 2, "used": 2},
        "work2": {"budget": 2, "used": 2},
    }
    edges = {"main": set(), "work2": {5}}
    assert handoff_broadcast(row, "main", players, edges) is None
