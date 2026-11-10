"""Блокировки слотов: asyncio в процессе + файловая блокировка между процессами."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

_SAFE_SLOT = re.compile(r"^[a-z0-9_\-]{1,64}$")


class SlotBusy(Exception):
    """Слот уже занят другой командой."""


class SlotLocker:
    def __init__(self, locks_dir: Path) -> None:
        self._dir = locks_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._gate = asyncio.Lock()
        self._held: dict[str, object] = {}

    async def acquire(self, slot: str) -> bool:
        key = self._safe(slot)
        async with self._gate:
            if key in self._held:
                return False
            handle = self._try_file_lock(key)
            if handle is None:
                return False
            self._held[key] = handle
            return True

    async def release(self, slot: str) -> None:
        key = self._safe(slot)
        async with self._gate:
            handle = self._held.pop(key, None)
        if handle is not None:
            self._unlock_file(handle)

    @staticmethod
    def _safe(slot: str) -> str:
        name = (slot or "db").strip().lower()
        if not _SAFE_SLOT.fullmatch(name):
            return "invalid"
        return name

    def _try_file_lock(self, key: str):
        path = self._dir / f"{key}.lock"
        handle = path.open("a+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"1")
                handle.flush()
            handle.seek(0)
            _lock_nb(handle)
            return handle
        except OSError:
            handle.close()
            return None

    @staticmethod
    def _unlock_file(handle) -> None:
        try:
            handle.seek(0)
            _unlock(handle)
        except OSError:
            pass
        handle.close()


def _lock_nb(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
