"""Оркестр: профили, общий набор групп, проверки на бота, раскладка без пересечений."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="ubb_orch_")
os.environ["USERBOT_DB"] = str(Path(_tmp) / "t.db")
os.environ["USERBOT_SESSIONS"] = str(Path(_tmp) / "sessions")

from control import orchestra  # noqa: E402
import bot  # noqa: E402


def test_group_key_normalizes_supergroup_ids():
    # join пишет -100…, диалоги отдают обрезанный id — ключ должен совпасть.
    assert bot.group_key(-1001234567890) == -1234567890
    assert bot.group_key(1234567890) == 1234567890
    assert bot.group_key(-1234567890) == -1234567890
    assert bot.group_key("42") == 42


def test_parse_profile_csv_skips_comments_and_fills_columns():
    text = "# аккаунт;имя;фамилия;о себе\nАнна; Иванова; Привет, я тут\n\nБоб\n"
    profiles = orchestra.parse_profile_csv(text)
    assert [p.name for p in profiles] == ["Анна", "Боб"]
    assert profiles[0].last_name == "Иванова"
    assert profiles[0].about == "Привет, я тут"
    assert profiles[1].last_name == "" and profiles[1].about == ""


def test_validate_profile_truncates_to_telegram_limits():
    profile, warnings = orchestra.validate_profile(
        orchestra.Profile("И" * 80, "Ф", "о" * 90))
    assert len(profile.name) == orchestra.NAME_LIMIT
    assert len(profile.about) == orchestra.ABOUT_LIMIT
    assert profile.name.endswith("…")
    assert warnings


def test_assign_identities_is_ordered_and_reports_leftovers():
    profiles = orchestra.parse_profile_csv("А;А;\nБ;Б;\nВ;В;")
    ids = orchestra.assign_identities(["main", "work2"], profiles, ["/tmp/a.jpg"])
    assert ids[0].profile.name == "А" and ids[0].avatar == "/tmp/a.jpg"
    assert ids[1].profile.name == "Б" and ids[1].avatar is None
    assert [p.name for p in orchestra.leftover_profiles(["main", "work2"], profiles)] == ["В"]


def test_validate_avatar_rejects_wrong_format_and_huge_file(tmp_path):
    ok = tmp_path / "a.jpg"
    ok.write_bytes(b"x")
    assert orchestra.validate_avatar(str(ok)) == []
    bad = tmp_path / "b.gif"
    bad.write_bytes(b"x")
    assert orchestra.validate_avatar(str(bad))
    huge = tmp_path / "c.png"
    with open(huge, "wb") as handle:
        handle.seek(orchestra.PHOTO_MAX_BYTES)
        handle.write(b"x")
    assert orchestra.validate_avatar(str(huge))


def test_plan_joins_reports_missing_and_private_without_link():
    groups = [
        {"id": 1, "title": "Публичная", "username": "pub"},
        {"id": 2, "title": "Приватная", "username": "", "invite_link": ""},
        {"id": 3, "title": "Приватная со ссылкой", "username": "", "invite_link": "https://t.me/+abc"},
    ]
    members = {"main": {1, 3}, "work2": set()}
    plans = orchestra.plan_joins(groups, members, ["main", "work2"])
    assert plans[0].missing == ["work2"] and not plans[0].link_needed
    assert plans[1].missing == ["main", "work2"] and plans[1].link_needed
    assert plans[2].missing == ["work2"] and not plans[2].link_needed


def test_stagger_joins_round_robins_with_limit():
    groups = [{"id": gid, "title": f"g{gid}", "username": f"g{gid}"} for gid in range(1, 5)]
    plans = orchestra.plan_joins(groups, {}, ["a", "b"])
    order = orchestra.stagger_joins(plans, limit_per_account=2)
    assert len(order) == 4
    accounts = [account for account, _ in order]
    assert accounts.count("a") == 2 and accounts.count("b") == 2
    # круг: первый идёт a, затем b — аккаунты не собираются в один блок
    assert accounts[:2] == ["a", "b"]


def test_botgate_candidates_only_clicks_whitelisted_buttons():
    buttons = [(0, 0, "Я не бот"), (0, 1, "Выйти"), (1, 0, "Report"),
               (1, 1, "Проверить"), (2, 0, "Оплатить")]
    picked = orchestra.botgate_candidates(buttons)
    assert [c.text for c in picked] == ["Я не бот", "Проверить"]
    assert orchestra.botgate_candidates([(0, 0, "star")]) == []


def test_is_gate_message_recognizes_captcha():
    assert orchestra.is_gate_message("Пройдите капчу, чтобы писать")
    assert orchestra.is_gate_message("Please verify you are human")
    assert not orchestra.is_gate_message("Привет всем")


def test_plan_conduct_never_assigns_one_target_twice():
    groups = [{"id": 1}, {"id": 2}]
    people = [{"user_id": 10}, {"user_id": 11}, {"user_id": 12}, {"user_id": 13}]
    players = {"a": {"budget": 3, "used": 0}, "b": {"budget": 1, "used": 0}}
    edges = {"a": {1}, "b": {1, 2}}
    plan = orchestra.plan_conduct(groups, people, players, edges)
    group_accounts = [item["account"] for item in plan["groups"]]
    people_accounts = [item["account"] for item in plan["people"]]
    assert len(group_accounts) == 2 and None not in group_accounts
    assert len(people_accounts) == 4
    # Бюджеты a=3, b=1; группа 1 достаётся a (единственный держатель), группа 2 — b.
    # Остаётся a:2, b:0 — двух людей разбирает a, двум не хватает места.
    assert group_accounts == ["a", "b"]
    assert people_accounts.count(None) == 2
    assert people_accounts.count("a") == 2


def test_coverage_matrix_counts_full_groups():
    groups = [{"id": 1, "title": "g1"}, {"id": 2, "title": "g2"}]
    edges = {"a": {1, 2}, "b": {1}}
    matrix = orchestra.coverage_matrix(groups, edges, ["a", "b"])
    assert matrix["per_account"] == {"a": 2, "b": 1}
    assert matrix["full_groups"] == [1]
