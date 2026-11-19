"""CLI entry point: wires RateLimiter, Poster, Booster and Scheduler together.

Standalone runner — no network calls at import time. The default sender is a
no-op stub; production code injects the real Telethon send through
``build_booster``.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Awaitable, Callable, Optional

from .booster import Booster, BudgetExceeded
from .poster import Poster
from .rate_limiter import RateLimiter
from .scheduler_core import Scheduler

__all__ = ["build_booster", "main"]


async def _noop_send(chat_id: Any, text: str) -> str:
    """Placeholder sender kept importable and testable without a Telegram session."""
    return f"ok-{chat_id}"


def build_booster(
    send: Callable[..., Awaitable[Any]],
    *,
    max_rate: float = 1.0,
    period: float = 1.0,
    budget: int = 1,
    window: float = 86400.0,
    clock: Optional[Callable[[], float]] = None,
) -> Booster:
    """Assemble the full delivery stack around a sender callable."""
    return Booster(
        Poster(send),
        RateLimiter(max_rate, period),
        budget=budget,
        window=window,
        clock=clock,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


async def _run(args: argparse.Namespace) -> None:
    booster = build_booster(
        _noop_send,
        max_rate=args.max_rate,
        period=args.period,
        budget=args.budget,
        window=args.window,
    )

    if args.interval is None:
        message_id = await booster.boost(args.chat_id, args.text)
        print(message_id)
        return

    async def safe_boost(chat_id: int, text: str) -> None:
        try:
            await booster.boost(chat_id, text)
        except BudgetExceeded as exc:
            print(f"skipped: {exc}")

    scheduler = Scheduler()
    scheduler.every(args.interval, lambda: asyncio.create_task(safe_boost(args.chat_id, args.text)))
    try:
        await scheduler.run()
    finally:
        scheduler.stop()


def main(argv: Optional[list[str]] = None) -> int:
    """Run one boost, or a scheduled sequence when ``--interval`` is given."""
    parser = argparse.ArgumentParser(prog="tg-userbot-boost", description="Send messages under rate and budget limits.")
    parser.add_argument("--chat-id", type=int, required=True, help="target chat identifier")
    parser.add_argument("--text", required=True, help="message text to send")
    parser.add_argument("--budget", type=_positive_int, default=1, help="max sends per window (default: 1)")
    parser.add_argument("--max-rate", type=_positive_float, default=1.0, help="token bucket capacity (default: 1)")
    parser.add_argument(
        "--period", type=_positive_float, default=1.0, help="token bucket period in seconds (default: 1)"
    )
    parser.add_argument(
        "--window", type=_positive_float, default=86400.0, help="budget window in seconds (default: 1 day)"
    )
    parser.add_argument(
        "--interval", type=_positive_float, default=None, help="repeat every N seconds until interrupted"
    )
    args = parser.parse_args(argv)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
