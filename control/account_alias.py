"""Маппинг внутренних имён аккаунтов на псевдонимы для Mini App UI.

Гарантирует, что реальные имена сессий (main, work2) и user_id аккаунтов
не попадают в клиентский интерфейс, логи или ответы API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AccountAlias:
    internal_name: str  # "main", "work2" — только для бэкенда
    display_name: str   # "Аккаунт 1", "Аккаунт 2" — для UI
    is_eligible: bool


class AccountAliasMapper:
    def __init__(self, internal_names: list[str] | None = None) -> None:
        self._aliases: dict[str, AccountAlias] = {}
        self._counter = 1
        for name in internal_names or []:
            self.register(name)

    def register(self, internal_name: str) -> AccountAlias:
        if internal_name in self._aliases:
            return self._aliases[internal_name]
        display = f"Аккаунт {self._counter}"
        self._counter += 1
        alias = AccountAlias(
            internal_name=internal_name,
            display_name=display,
            is_eligible=True,
        )
        self._aliases[internal_name] = alias
        return alias

    def to_alias(self, internal_name: str) -> AccountAlias:
        if internal_name not in self._aliases:
            return self.register(internal_name)
        return self._aliases[internal_name]

    def to_internal(self, display_name: str) -> str | None:
        for alias in self._aliases.values():
            if alias.display_name == display_name:
                return alias.internal_name
        return None

    def list_aliases(self) -> list[AccountAlias]:
        return list(self._aliases.values())

    def mask_internal_name(self, internal_name: str) -> str:
        """Возвращает display_name для использования в логах/UI."""
        return self.to_alias(internal_name).display_name

    @staticmethod
    def is_valid_internal_name(name: str) -> bool:
        return bool(re.fullmatch(r"^[a-z0-9_\-]{1,32}$", name or ""))
