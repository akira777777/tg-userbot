"""Слоты аккаунтов: файловая блокировка не должна мешать самому воркеру."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from control.slots import SlotLocker


def test_release_frees_slot_for_next_command(tmp_path: Path) -> None:
    locker = SlotLocker(tmp_path)

    async def scenario() -> tuple[bool, bool, bool]:
        first = await locker.acquire("main")
        second = await locker.acquire("main")  # тот же процесс: слот занят
        await locker.release("main")
        third = await locker.acquire("main")  # после освобождения снова можно
        await locker.release("main")
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert third is True


def test_held_handle_survives_descriptor_work(tmp_path: Path) -> None:
    """Регрессия: после захвата файл оставался с указателем на байте 0.

    На Windows msvcrt.locking держит байт по позиции указателя, и любые
    последующие операции с этим дескриптором падали EACCES — Telethon
    получал «database is locked». Проверяем, что после захвата дескриптор
    можно читать и записывать.
    """
    locker = SlotLocker(tmp_path)

    async def scenario() -> None:
        assert await locker.acquire("main") is True
        handle = locker._held["main"]
        # Повторные файловые операции на удерживаемом дескрипторе не бросают OSError.
        handle.seek(1)
        handle.write(b"2")
        handle.flush()
        handle.seek(0)
        assert handle.read(2) == b"12"
        await locker.release("main")

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="между процессами проверяется на POSIX")
def test_second_locker_cannot_take_busy_slot(tmp_path: Path) -> None:
    first = SlotLocker(tmp_path)
    second = SlotLocker(tmp_path)

    async def scenario() -> tuple[bool, bool]:
        assert await first.acquire("main") is True
        taken = await second.acquire("main")
        await first.release("main")
        return taken, await second.acquire("main")

    taken, after_release = asyncio.run(scenario())
    assert taken is False
    assert after_release is True
