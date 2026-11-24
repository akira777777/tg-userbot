"""Чистая математика пауз и таймингов: FloodWait/PeerFlood, окно до полуночи UTC.

Без I/O: только аргументы и стандартная библиотека. Вынесено из bot.py
(ADR-0001, стадия 1); bot.py реэкспортирует имена для совместимости.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

FLOOD_JITTER = 1.15          # 15% сверху к серверной паузе, чтобы не биться синхронно
BOTH_SLEEP_CAP = 30

__all__ = [
    "BOTH_SLEEP_CAP",
    "FLOOD_JITTER",
    "earliest_moment",
    "flood_pause_until",
    "flood_wait_seconds",
    "next_utc_midnight",
    "ordered_pause",
    "peerflood_backoff",
    "peerflood_pause_until",
    "wait_seconds",
]


def ordered_pause(delay_min, delay_max):
    """Пара паузы для random.uniform. Перепутанные границы меняются местами."""
    lo = float(delay_min or 0)
    hi = float(delay_max or 0)
    if lo < 0:
        lo = 0.0
    if hi < 0:
        hi = 0.0
    if hi > 0 and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def flood_wait_seconds(exc, jitter=None):
    """Сколько реально спать после FloodWait: секунды сервера + jitter.
    Минимум 1 с, чтобы не крутить пустой цикл."""
    try:
        seconds = int(getattr(exc, "seconds", 0) or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds < 1:
        seconds = 1
    factor = FLOOD_JITTER if jitter is None else max(1.0, float(jitter))
    # Округление вверх: пауза никогда не короче серверной, 100 с → 115 с.
    return min(math.ceil(seconds * factor), 86400)


def flood_pause_until(exc, now=None):
    """Пауза на число секунд от сервера (+15% jitter). Минимум 1 с."""
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(seconds=flood_wait_seconds(exc))).isoformat(timespec="seconds")


def peerflood_backoff(strikes):
    """Пауза после PEER_FLOOD. Первый раз — сутки, дальше экспоненциально,
    с потолком 30 дней: «повторные нарушения — дольше, упорство — навсегда»."""
    n = max(1, int(strikes or 1))
    hours = min(24 * (2 ** (n - 1)), 24 * 30)
    return timedelta(hours=hours)


def peerflood_pause_until(strikes=1, now=None):
    now = now or datetime.now(timezone.utc)
    return (now + peerflood_backoff(strikes)).isoformat(timespec="seconds")


def earliest_moment(now, deadlines):
    """Ближайший момент в будущем. None, если ждать нечего."""
    future = []
    for item in deadlines:
        if not item:
            continue
        if isinstance(item, str):
            try:
                item = datetime.fromisoformat(item)
            except ValueError:
                continue
        if item.tzinfo is None:
            item = item.replace(tzinfo=timezone.utc)
        if item > now:
            future.append(item)
    return min(future) if future else None


def next_utc_midnight(now):
    now = now.astimezone(timezone.utc)
    nxt = now.date() + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=timezone.utc)


def wait_seconds(now, moment, cap=BOTH_SLEEP_CAP):
    """Сколько спать до момента, но не дольше cap: так Стоп замечается без долгой паузы."""
    if moment is None:
        return float(cap)
    return max(0.0, min(float(cap), (moment - now).total_seconds()))
