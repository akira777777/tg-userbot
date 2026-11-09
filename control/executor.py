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
    CommandKind,
    CommandMode,
    CommandRecord,
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
        argv = self._build_argv(kind, payload, account, record.request.mode)
        if argv is None:
            return {
                "ok": False,
                "error": f"неподдерживаемая команда: {kind}",
                "redacted_account": self._alias_mapper.mask_internal_name(account),
            }

        cmd = [self._python, str(self._root / "bot.py")] + argv
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
        payload: Any,
        account: str,
        mode: CommandMode = CommandMode.DRY_RUN,
    ) -> list[str] | None:
        """Переводит типизированный payload в argv bot.py."""
        data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload or {})
        if kind == CommandKind.STATUS:
            return ["status", "--account", account]
        if kind == CommandKind.GROUPS:
            return ["groups", "--list", "--account", account]
        if kind == CommandKind.CAMPAIGNS:
            return ["status", "--account", account]
        if kind == CommandKind.MEDIA_LIST:
            return ["media", "list", "--account", account]
        if kind == CommandKind.BLACKLIST_ADD:
            value = str(data.get("value") or "").strip()
            if not value:
                return None
            return ["blacklist", "add", value, "--reason", str(data.get("reason") or "вручную")]
        if kind == CommandKind.STOP:
            args = ["stop"]
            target = data.get("account") or account
            if target:
                args += ["--account", str(target)]
            return args
        if kind == CommandKind.BROADCAST:
            args = ["broadcast", "--account", account]
            self._append_delivery_args(args, data)
            if data.get("repeat_ok"):
                args += ["--repeat-ok"]
            return self._with_mode(args, mode)
        if kind == CommandKind.DMSEND:
            args = ["dmsend", "--account", account, "--limit", str(data.get("limit") or 20)]
            self._append_delivery_args(args, data)
            if data.get("group_id"):
                args += ["--group", str(data["group_id"])]
            if data.get("repeat_ok"):
                args += ["--repeat-ok"]
            return self._with_mode(args, mode)
        if kind == CommandKind.CONDUCT:
            if data.get("kind") not in {"broadcast", "dm"}:
                return None
            args = ["conduct", "--what", data["kind"]]
            self._append_delivery_args(args, data)
            if data.get("group_id"):
                args += ["--group", str(data["group_id"])]
            if data.get("limit"):
                args += ["--limit", str(data["limit"])]
            if data.get("window_hours"):
                args += ["--window", str(data["window_hours"])]
            if data.get("repeat_ok"):
                args += ["--repeat-ok"]
            return self._with_mode(args, mode)
        return None

    @staticmethod
    def _append_delivery_args(args: list[str], data: dict) -> None:
        if data.get("text"):
            args += ["--text", str(data["text"])]
        if data.get("media_id"):
            args += ["--media", str(data["media_id"])]
        if data.get("group_ids"):
            args += ["--ids", *[str(group_id) for group_id in data["group_ids"]]]
        if data.get("delay_min"):
            args += ["--delay-min", str(data["delay_min"])]
        if data.get("delay_max"):
            args += ["--delay-max", str(data["delay_max"])]
        if data.get("max_runs"):
            args += ["--max-runs", str(data["max_runs"])]

    @staticmethod
    def _with_mode(args: list[str], mode: CommandMode) -> list[str]:
        if mode != CommandMode.LIVE:
            args += ["--dry-run"]
        return args
