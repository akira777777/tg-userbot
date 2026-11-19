"""CLI bot.py не открывает session-файл, уже занятый панелью/воркером."""

from __future__ import annotations

import pytest

import bot
from control.slots import SlotLocker


def test_hold_account_slot_respects_parent_env(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "LOCKS_DIR", tmp_path)
    monkeypatch.setattr(bot, "_slot_locker", None)
    bot._covered_slots.clear()
    bot._locked_slots.clear()
    monkeypatch.setenv("USERBOT_SLOT_HELD", "main")

    bot.hold_account_slot("main")
    assert "main" in bot._covered_slots
    assert "main" not in bot._locked_slots  # родитель уже держит file-lock


def test_hold_account_slot_blocks_busy_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "LOCKS_DIR", tmp_path)
    monkeypatch.setattr(bot, "_slot_locker", None)
    bot._covered_slots.clear()
    bot._locked_slots.clear()
    monkeypatch.delenv("USERBOT_SLOT_HELD", raising=False)

    holder = SlotLocker(tmp_path)
    assert holder.try_acquire_sync("work2") is True
    with pytest.raises(SystemExit, match="Слот «work2» занят"):
        bot.hold_account_slot("work2")
    holder.release_sync("work2")


def test_hold_account_slot_acquires_for_direct_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "LOCKS_DIR", tmp_path)
    monkeypatch.setattr(bot, "_slot_locker", None)
    bot._covered_slots.clear()
    bot._locked_slots.clear()
    monkeypatch.delenv("USERBOT_SLOT_HELD", raising=False)

    bot.hold_account_slot("main")
    assert "main" in bot._locked_slots
    other = SlotLocker(tmp_path)
    assert other.try_acquire_sync("main") is False
    bot.release_account_slots()
    assert other.try_acquire_sync("main") is True
    other.release_sync("main")
