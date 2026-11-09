"""Исполнитель команд: маппит CommandRecord на типизированные операции userbot.

Гарантирует:
- одну активную команду на аккаунт/кондуктор
- checkpoint отмены между целями
- терминальный статус ровно один раз
- приватность forward (без forward_messages)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from control.account_alias import AccountAliasMapper
from control.config import ControlSettings
from control.models import (
    BlacklistAddPayload,
    BroadcastPayload,
    CommandKind,
    CommandRecord,
    ConductPayload,
    DmSendPayload,
    ReadPayload,
    StopPayload,
)
from control.privacy_forward import ForwardPrivacyManager


class UserbotRuntime:
    """Абстракция над запуском bot.py (CLI или прямой импорт в тестах)."""

    def __init__(self, settings: ControlSettings, project_root: Path) -> None:
        self._settings = settings
        self._root = project_root
        self._python = sys.executable
        self._alias_mapper = AccountAliasMapper()
        self._forward_privacy = ForwardPrivacyManager(enabled=settings.forward_privacy)

    async def run_command(self, record: CommandRecord) -> dict[str, Any]:
        """Выполняет одну команду и возвращает structured result."""
        kind = record.request.kind
        payload = record.request.payload
        account = record.request.account

        # Регистрируем аккаунт для псевдонимов
        self._alias_mapper.register(account)

        # Формируем argv для bot.py
        argv = self._build_argv(kind, payload, account)
        if argv is None:
            return {
                "ok": False,
                "error": f"неподдерживаемая команда: {kind}",
                "redacted_account": self._alias_mapper.mask_internal_name(account),
            }

        cmd = [self._python, str(self._root / "bot.py"), *argv]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._root),
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace")
            error_output = stderr.decode("utf-8", errors="replace")

            # Редакция вывода перед возвратом
            output = self._forward_privacy.redact_log_line(output)
            error_output = self._forward_privacy.redact_log_line(error_output)

            if proc.returncode == 0:
                return {
                    "ok": True,
                    "output": output[-2000:] if output else "",
                    "redacted_account": self._alias_mapper.mask_internal_name(account),
                }
            return {
                "ok": False,
                "error": error_output[-500:] if error_output else f"exit code {proc.returncode}",
                "redacted_account": self._alias_mapper.mask_internal_name(account),
            }
        except Exception as exc:  # pragma: no cover - сетевые/процессные ошибки
            return {
                "ok": False,
                "error": self._forward_privacy.redact_log_line(str(exc)),
                "redacted_account": self._alias_mapper.mask_internal_name(account),
            }

    def _build_argv(
        self,
        kind: CommandKind,
        payload: BroadcastPayload | DmSendPayload | ConductPayload | ReadPayload | BlacklistAddPayload | StopPayload,
        account: str,
    ) -> list[str] | None:
        """Переводит типизированный payload в argv bot.py."""
        if kind == CommandKind.STATUS:
            return ["status", "--account", account]
        if kind == CommandKind.GROUPS:
            return ["groups", "--list", "--account", account]
        if kind == CommandKind.CAMPAIGNS:
            return ["status", "--account", account]
        if kind == CommandKind.MEDIA_LIST:
            return ["media", "list", "--account", account]
        if kind == CommandKind.BROADCAST:
            if not isinstance(payload, BroadcastPayload):
                return None
            args = ["broadcast", "--account", account]
            if payload.text:
                args += ["--text", payload.text]
            if payload.media_id:
                args += ["--media", str(payload.media_id)]
            if payload.group_ids:
                args += ["--ids"] + [str(g) for g in payload.group_ids]
            if payload.delay_min > 0:
                args += ["--delay-min", str(payload.delay_min)]
            if payload.delay_max > 0:
                args += ["--delay-max", str(payload.delay_max)]
            if payload.max_runs > 0:
                args += ["--max-runs", str(payload.max_runs)]
            args += ["--dry-run"]  # по умолчанию dry-run
            return args
        if kind == CommandKind.DMSEND:
            if not isinstance(payload, DmSendPayload):
                return None
            args = ["dmsend", "--account", account]
            if payload.text:
                args += ["--text", payload.text]
            if payload.media_id:
                args += ["--media", str(payload.media_id)]
            if payload.group_id:
                args += ["--group", str(payload.group_id)]
            args += ["--limit", str(payload.limit)]
            if payload.delay_min > 0:
                args += ["--delay-min", str(payload.delay_min)]
            if payload.delay_max > 0:
                args += ["--delay-max", str(payload.delay_max)]
            if payload.repeat_ok:
                args += ["--repeat-ok"]
            args += ["--dry-run"]
            return args
        if kind == CommandKind.CONDUCT:
            if not isinstance(payload, ConductPayload):
                return None
            args = ["conduct", "--what", payload.kind, "--account", account]
            if payload.text:
                args += ["--text", payload.text]
            if payload.media_id:
                args += ["--media", str(payload.media_id)]
            if payload.group_ids:
                args += ["--ids"] + [str(g) for g in payload.group_ids]
            if payload.group_id:
                args += ["--group", str(payload.group_id)]
            if payload.limit > 0:
                args += ["--limit", str(payload.limit)]
            if payload.window_hours > 0:
                args += ["--window", str(payload.window_hours)]
            if payload.repeat_ok:
                args += ["--repeat-ok"]
            args += ["--dry-run"]
            return args
        if kind == CommandKind.STOP:
            # bot.py has no stop subcommand; cancellation stays in the queue worker.
            return None
        return None
