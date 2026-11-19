"""Менеджер приватности пересылки (forward).

Гарантирует, что сообщения отправляются без раскрытия исходного отправителя:
- никогда не используем forward_messages
- используем send_message / send_file
- удаляем forward_from, forward_from_chat, forward_date из логов и ответов API
"""

from __future__ import annotations

import re
from typing import Any

from telethon import TelegramClient

FORWARD_FIELDS: tuple[str, ...] = (
    "forward_from",
    "forward_from_chat",
    "forward_from_name",
    "forward_date",
    "forward_sender_name",
)


class ForwardPrivacyManager:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def strip_message_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Удаляет forward-атрибуцию из словаря сообщения перед логом/API."""
        if not self.enabled:
            return data
        result = dict(data)
        for field in FORWARD_FIELDS:
            result.pop(field, None)
        # Редакция вложенных полей
        for key in ("from", "sender", "peer"):
            if key in result and isinstance(result[key], dict):
                result[key] = self._redact_user_dict(result[key])
        return result

    def _redact_user_dict(self, user: dict[str, Any]) -> dict[str, Any]:
        result = dict(user)
        for sensitive in ("id", "phone", "username"):
            if sensitive in result:
                result[sensitive] = "<redacted>"
        return result

    async def send_safe(
        self,
        client: TelegramClient,
        peer: Any,
        message: str | None = None,
        file: str | bytes | Any | None = None,
        caption: str | None = None,
    ) -> Any:
        """Отправляет сообщение без forward-атрибуции.

        Использует send_message для текста, send_file для медиа.
        Никогда не вызывает forward_messages.
        """
        if file is not None:
            return await client.send_file(peer, file, caption=caption or message or None)
        if message:
            return await client.send_message(peer, message)
        raise ValueError("нужно текст или файл для отправки")

    @staticmethod
    def redact_log_line(line: str, *, max_len: int | None = 2000, keep_tail: bool = False) -> str:
        """Редакция строки лога: удаляет user_id, пути сессий (POSIX и Windows), токены и ключи."""
        result = line
        result = re.sub(r"(?:API_ID(?:_\w+)?)\s*[=:]\s*\S+", "API_ID=***", result, flags=re.IGNORECASE)
        result = re.sub(r"(?:API_HASH(?:_\w+)?)\s*[=:]\s*\S+", "API_HASH=***", result, flags=re.IGNORECASE)
        result = re.sub(
            r"(?:BOT_TOKEN|CONTROL_SESSION_SECRET|SUPABASE_(?:SECRET|SERVICE_ROLE)_KEY)\s*[=:]\s*\S+",
            "<secret>=***",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b", "<bot_token>", result)
        result = re.sub(r"sb_secret_\S+", "sb_secret=***", result)
        result = re.sub(
            r"(?:[A-Za-z]:)?(?:[\\/]?[^\\/\s'\"`]+[\\/])*[A-Za-z0-9_.\-]+\.session(?:-journal|-wal|-shm|\.unauth)?\b",
            "<session_file>",
            result,
        )
        result = re.sub(r"\b\d{9,15}\b", "<user_id>", result)
        result = re.sub(r"\b[A-Za-z0-9+/]{40,}={0,2}\b", "<token>", result)
        if max_len is None or len(result) <= max_len:
            return result
        return result[-max_len:] if keep_tail else result[:max_len]
