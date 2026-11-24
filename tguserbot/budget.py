"""Чистая математика бюджетов рассылки: дневной потолок выборки и контингент аккаунта.

Без I/O: только аргументы. Вынесено из bot.py (ADR-0001, стадия 1);
bot.py реэкспортирует имена для совместимости.
"""

from __future__ import annotations

# Бюджет DM по возрасту аккаунта (не официальный лимит Telegram, а эвристика
# из docs/telegram/limits-and-antispam.md). Ключ — code из account_cohort.
COHORT_BUDGET = {"fresh": 15, "warm": 30, "mature": 60, "unknown": 20}

# Человекочитаемые названия контингентов для status/accounts.
COHORT_RU = {
    "fresh": "свежий (<2 недель)",
    "warm": "прогретый (2 недели — 3 месяца)",
    "mature": "зрелый (>3 месяцев)",
    "unknown": "дата регистрации неизвестна",
}

__all__ = ["COHORT_BUDGET", "COHORT_RU", "account_cohort", "cohort_budget", "trim_to_cap"]


def trim_to_cap(rows, cap, used):
    """Обрезать выборку дневным потолком. cap=0 — без лимита, left=None."""
    if not cap:
        return list(rows), None
    left = max(0, int(cap) - int(used))
    return list(rows)[:left], left


def account_cohort(age_days, config_cohort="auto"):
    """Контингент аккаунта по возрасту: fresh | warm | mature | unknown."""
    if config_cohort and config_cohort != "auto":
        return config_cohort
    if age_days is None:
        return "unknown"
    try:
        days = float(age_days)
    except (TypeError, ValueError):
        return "unknown"
    if days < 14:
        return "fresh"
    if days < 90:
        return "warm"
    return "mature"


def cohort_budget(cohort, base=None):
    """Бюджет DM на сутки. Явный бюджет аккаунта не понижает, но не поднимает
    выше потолка контингента: age не обходится флагом CLI."""
    cap = COHORT_BUDGET.get(cohort, COHORT_BUDGET["unknown"])
    if base is None or int(base) <= 0:
        return cap
    return min(int(base), cap)
