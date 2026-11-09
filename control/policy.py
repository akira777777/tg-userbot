"""Центральная политика операций: allowlist, лимиты, подтверждения, редакция.

Никакой работы с БД или сетью — только чистая валидация.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final

from control.config import ControlSettings
from control.models import (
    BroadcastPayload,
    CommandKind,
    CommandMode,
    CommandPayload,
    ConductPayload,
    DmSendPayload,
    TelegramIdentity,
    ValidatedCommand,
)

# --- 재사емые константы ---
READ_ONLY_KINDS: Final[frozenset[CommandKind]] = frozenset(
    {CommandKind.STATUS, CommandKind.GROUPS, CommandKind.CAMPAIGNS, CommandKind.MEDIA_LIST}
)
LIVE_REQUIRES_CONFIRMATION: Final[frozenset[CommandKind]] = frozenset(
    {CommandKind.BROADCAST, CommandKind.DMSEND, CommandKind.CONDUCT}
)
MAX_TEXT_LENGTH: Final = 4000
MAX_GROUP_IDS: Final = 200
MAX_DM_LIMIT: Final = 200
REDACTION_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r"API_ID_\w+\s*=\s*\S+"), "API_ID=***"),
    (re.compile(r"API_HASH_\w+\s*=\s*\S+"), "API_HASH=***"),
    (re.compile(r"sb_secret_\S+"), "sb_secret=***"),
    (re.compile(r"\b\d{9,15}\b"), "<user_id>"),
    (re.compile(r"/[A-Za-z0-9_\-/]+\.session\b"), "<session_file>"),
    (re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "<token>"),
]


class Redactor:
    """Удаляет секреты, пути сессий, длинные user_id из текста перед записью в логи/UI."""

    @classmethod
    def redact(cls, text: str) -> str:
        result = text
        for pattern, replacement in REDACTION_PATTERNS:
            result = pattern.sub(replacement, result)
        return result[:2000]

    @classmethod
    def redact_account_name(cls, internal_name: str) -> str:
        """Маппит внутреннее имя аккаунта на псевдоним для UI."""
        # В фазе 1 — простая маска, в фазе 2 подключается AccountAliasMapper
        m = re.match(r"^(main|work\d+|acc\d+)$", internal_name or "")
        if not m:
            return "<account>"
        num = m.group(1)
        if num == "main":
            return "Аккаунт 1"
        digits = re.sub(r"\D", "", num)
        return f"Аккаунт {int(digits) + 1}" if digits else "Аккаунт"


class CommandPolicy:
    """Неизменяемая конфигурация политики и методы валидации."""

    def __init__(self, settings: ControlSettings) -> None:
        self._settings = settings
        self._owner_ids = settings.owner_telegram_ids
        self._forward_privacy = settings.forward_privacy

    def is_owner(self, identity: TelegramIdentity) -> bool:
        return identity.telegram_user_id in self._owner_ids

    def validate_request(
        self,
        identity: TelegramIdentity,
        request: CommandPayload,
        now: datetime | None = None,
    ) -> dict:
        """Возвращает {'ok': True, 'validated': {...}} или {'ok': False, 'error': str}."""
        now = now or datetime.now(UTC)

        # Проверка kind/mode сочетаний
        if request.mode == CommandMode.LIVE and request.kind in LIVE_REQUIRES_CONFIRMATION:
            if not request.confirmation_nonce:
                return {
                    "ok": False,
                    "error": "LIVE режим требует confirmation_nonce",
                }

        # Ограничения по payload
        if request.kind in (CommandKind.BROADCAST, CommandKind.DMSEND, CommandKind.CONDUCT):
            payload = request.payload
            if isinstance(payload, BroadcastPayload):
                if len(payload.group_ids) > MAX_GROUP_IDS:
                    return {"ok": False, "error": "слишком много group_ids"}
                if payload.text and len(payload.text) > MAX_TEXT_LENGTH:
                    return {"ok": False, "error": "текст слишком длинный"}
            elif isinstance(payload, DmSendPayload):
                if payload.limit > MAX_DM_LIMIT:
                    return {"ok": False, "error": "превышен лимит получателей DM"}
                if payload.text and len(payload.text) > MAX_TEXT_LENGTH:
                    return {"ok": False, "error": "текст слишком длинный"}
            elif isinstance(payload, ConductPayload):
                if len(payload.group_ids) > MAX_GROUP_IDS:
                    return {"ok": False, "error": "слишком много group_ids"}
                if payload.text and len(payload.text) > MAX_TEXT_LENGTH:
                    return {"ok": False, "error": "текст слишком длинный"}

        # Idempotency key всегда должен быть установлен
        if not request.idempotency_key:
            return {"ok": False, "error": "отсутствует idempotency_key"}

        return {
            "ok": True,
            "validated": {
                "kind": request.kind,
                "mode": request.mode,
                "account": request.account,
                "payload": request.payload.model_dump(mode="json"),
                "idempotency_key": str(request.idempotency_key),
                "confirmation_nonce": (
                    str(request.confirmation_nonce) if request.confirmation_nonce else None
                ),
                "forward_privacy": self._forward_privacy,
                "requested_by": identity.telegram_user_id,
            },
        }

    def requires_confirmation(self, kind: CommandKind, mode: CommandMode) -> bool:
        return mode == CommandMode.LIVE and kind in LIVE_REQUIRES_CONFIRMATION

    def is_read_only(self, kind: CommandKind) -> bool:
        return kind in READ_ONLY_KINDS

    def safe_summary(self, kind: CommandKind, payload: dict) -> str:
        """Краткое описание операции без раскрытия user_id получателей."""
        parts: list[str] = [kind.value]
        if isinstance(payload, dict):
            if payload.get("text"):
                parts.append(f"text={payload['text'][:60]}...")
            if payload.get("group_ids"):
                parts.append(f"targets={len(payload['group_ids'])}")
            if payload.get("limit"):
                parts.append(f"limit={payload['limit']}")
        return " | ".join(parts)

    @classmethod
    def redact_text(cls, text: str) -> str:
        return Redactor.redact(text)

    @classmethod
    def account_alias(cls, internal_name: str) -> str:
        return Redactor.redact_account_name(internal_name)


class ValidatedCommand:
    """Результат успешной валидации, готовый к enqueue."""

    def __init__(
        self,
        identity: TelegramIdentity,
        request: CommandPayload,
        validated: dict,
    ) -> None:
        self.identity = identity
        self.request = request
        self.validated = validated
