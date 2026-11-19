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
from control.policy import Redactor
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
        self._alias_mapper = AccountAliasMapper(self._known_accounts())
        self._forward_privacy = ForwardPrivacyManager(enabled=settings.forward_privacy)
        self._locker = locker or SlotLocker(project_root / "locks")
        self._procs: dict[UUID, asyncio.subprocess.Process] = {}

    def _known_accounts(self) -> list[str]:
        """Все имена аккаунтов из userbot.db для детерминированных псевдонимов (DASH-603)."""
        db_path = Path(os.environ.get("USERBOT_DB", self._root / "userbot.db"))
        if not db_path.exists():
            return []
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            return [row[0] for row in conn.execute("SELECT name FROM accounts ORDER BY name") if row[0]]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _mask_account(self, account: str) -> str:
        if account == "all":
            return "Все аккаунты"
        known = self._known_accounts()
        names = set(known) | {alias.internal_name for alias in self._alias_mapper.list_aliases()}
        if account:
            names.add(account)
        self._alias_mapper = AccountAliasMapper(list(names))
        return self._alias_mapper.mask_internal_name(account)

    async def run_command(self, record: CommandRecord) -> dict[str, Any]:
        """Выполняет одну команду и возвращает structured result."""
        kind = record.request.kind
        payload = record.request.payload
        account = record.request.account
        self._mask_account(account)
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

    def _redact(self, text: str, *, max_len: int = 2000, keep_tail: bool = False) -> str:
        cleaned = text or ""
        secrets = [
            self._settings.bot_token,
            self._settings.session_secret,
            self._settings.supabase_service_role_key,
        ]
        for env_key, env_val in os.environ.items():
            upper = env_key.upper()
            if (
                upper == "API_HASH"
                or upper.startswith("API_HASH_")
                or upper in {"BOT_TOKEN", "CONTROL_SESSION_SECRET", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY", "DASH_TOKEN"}
            ):
                if env_val:
                    secrets.append(env_val.strip())
        for secret in secrets:
            if secret and len(secret) >= 8:
                cleaned = cleaned.replace(secret, "***")
        no_privacy = self._forward_privacy.redact_log_line(cleaned, max_len=None)
        return Redactor.redact(no_privacy, max_len=max_len, keep_tail=keep_tail)

    def _result(self, code: int | None, stdout: bytes, stderr: bytes, account: str) -> dict[str, Any]:
        output = self._redact(stdout.decode("utf-8", errors="replace"), max_len=2000, keep_tail=True)
        raw_err = stderr.decode("utf-8", errors="replace")
        error_output = self._redact(raw_err, max_len=500, keep_tail=True)
        if code == 0:
            return {
                "ok": True,
                "output": output,
                "redacted_account": self._mask_account(account),
            }
        return self._fail(
            account,
            error_output if error_output else f"exit code {code}",
            code=code,
            raw_error=raw_err,
        )

    def _fail(
        self,
        account: str,
        error: str,
        code: int | None = None,
        *,
        raw_error: str | None = None,
    ) -> dict[str, Any]:
        redacted = self._redact(error, max_len=500, keep_tail=True)
        classification_source = raw_error if raw_error is not None else redacted
        return {
            "ok": False,
            "error": redacted,
            # error_code идёт в отдельную колонку и в UI: короткий стабильный код,
            # а не кусок traceback (там бывает user_id и путь к сессии).
            "error_code": classify_error(classification_source, code),
            "redacted_account": self._mask_account(account),
        }


def classify_error(text: str, code: int | None = None) -> str:
    """Короткий код ошибки для очереди и панели вместо сырого вывода процесса."""
    lowered = (text or "").lower()
    checks = (
        ("timeout", "timeout"),
        ("floodwait", "flood_wait"),
        ("flood_wait", "flood_wait"),
        ("database is locked", "db_locked"),
        ("session file is locked", "session_locked"),
        ("the process cannot access the file", "session_locked"),
        ("unauthorized", "unauthorized"),
        ("api_id", "auth_config"),
        ("api_hash", "auth_config"),
        ("busy", "busy"),
    )
    for needle, label in checks:
        if needle in lowered:
            return label
    if code is not None and code != 0:
        return f"exit_{code}"
    return "failed"


async def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except TimeoutError:
        proc.kill()
        await proc.wait()