"""Исполнитель команд: маппит CommandRecord на типизированные операции userbot.

Гарантирует:
- одну активную команду на аккаунт/кондуктор
- checkpoint отмены между целями
- терминальный статус ровно один раз
- приватность forward (без forward_messages)
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from control.account_alias import AccountAliasMapper
from control.cli_argv import build_control_cli
from control.config import ControlSettings
from control.models import CommandKind, CommandMode, CommandRecord
from control.privacy_forward import ForwardPrivacyManager
from control.slots import SlotLocker


class UserbotRuntime:
    """Абстракция над запуском bot.py (CLI или прямой импорт в тестах)."""

    def __init__(
        self,
        settings: ControlSettings,
        project_root: Path,
        locker: SlotLocker | None = None,
    ) -> None:
        self._settings = settings
        self._root = project_root
        self._python = sys.executable
        self._alias_mapper = AccountAliasMapper()
        self._forward_privacy = ForwardPrivacyManager(enabled=settings.forward_privacy)
        self._locker = locker or SlotLocker(project_root / "locks")
        self._procs: dict[UUID, asyncio.subprocess.Process] = {}

    async def run_command(self, record: CommandRecord) -> dict[str, Any]:
        """Выполняет одну команду и возвращает structured result."""
        kind = record.request.kind
        payload = record.request.payload
        account = record.request.account
        self._alias_mapper.register(account)
        spec = build_control_cli(kind, payload, account, record.request.mode)
        if spec is None:
            return self._fail(account, f"неподдерживаемая команда: {kind}")
        slots = [spec.slot_key]
        if kind == CommandKind.CONDUCT:
            for name in self._conduct_accounts(account):
                if name not in slots:
                    slots.append(name)
        acquired: list[str] = []
        try:
            for slot in slots:
                if not await self._locker.acquire(slot):
                    return self._fail(account, "busy")
                acquired.append(slot)
            return await self._run_locked(record, spec.argv, account)
        finally:
            for slot in reversed(acquired):
                await self._locker.release(slot)

    def _conduct_accounts(self, account: str) -> list[str]:
        """Сессии, которые conduct реально откроет. Их слоты нельзя отдать другой команде."""
        if account and account not in {"all", "conductor"}:
            return [account]
        db_path = Path(os.environ.get("USERBOT_DB", self._root / "userbot.db"))
        if not db_path.exists():
            return []
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            return [row[0] for row in conn.execute(
                "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    async def terminate_command(self, command_id: UUID) -> bool:
        proc = self._procs.get(command_id)
        if proc is None:
            return False
        await _terminate_proc(proc)
        return True

    def _build_argv(
        self,
        kind: CommandKind,
        payload: Any,
        account: str,
        mode: CommandMode = CommandMode.DRY_RUN,
    ) -> list[str] | None:
        spec = build_control_cli(kind, payload, account, mode)
        return None if spec is None else spec.argv

    async def _run_locked(self, record: CommandRecord, argv: list[str], account: str) -> dict[str, Any]:
        cmd = [self._python, str(self._root / "bot.py")] + argv
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._root),
            )
            self._procs[record.id] = proc
            stdout, stderr = await self._wait_proc(proc)
            return self._result(proc.returncode, stdout, stderr, account)
        except Exception as exc:  # pragma: no cover - сетевые/процессные ошибки
            return self._fail(account, str(exc))
        finally:
            self._procs.pop(record.id, None)

    async def _wait_proc(self, proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        timeout = float(self._settings.command_timeout_seconds or 0)
        if timeout <= 0:
            return await proc.communicate()
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            await _terminate_proc(proc)
            return b"", b"timeout"

    def _result(self, code: int | None, stdout: bytes, stderr: bytes, account: str) -> dict[str, Any]:
        output = self._forward_privacy.redact_log_line(stdout.decode("utf-8", errors="replace"))
        error_output = self._forward_privacy.redact_log_line(stderr.decode("utf-8", errors="replace"))
        if code == 0:
            return {
                "ok": True,
                "output": output[-2000:] if output else "",
                "redacted_account": self._alias_mapper.mask_internal_name(account),
            }
        return self._fail(account, error_output[-500:] if error_output else f"exit code {code}")

    def _fail(self, account: str, error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self._forward_privacy.redact_log_line(error),
            "redacted_account": self._alias_mapper.mask_internal_name(account),
        }


async def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except TimeoutError:
        proc.kill()
        await proc.wait()
