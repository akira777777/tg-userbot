"""Message poster: sends through an injected async sender with bounded retries."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

__all__ = ["Poster", "PostError", "PostResult"]


class PostError(Exception):
    """Raised when a message could not be posted after all attempts."""


class PostResult:
    """Outcome of one ``post_many`` item."""

    __slots__ = ("chat_id", "ok", "error", "message_id")

    def __init__(self, chat_id: Any, ok: bool, error: Optional[str] = None, message_id: Any = None) -> None:
        self.chat_id = chat_id
        self.ok = ok
        self.error = error
        self.message_id = message_id


class Poster:
    """Retrying poster.

    ``send`` is an async callable ``await send(chat_id, text) -> message_id``.
    Transient failures are retried up to ``attempts`` times with ``backoff``
    seconds between tries; the last error is wrapped in :class:`PostError`.
    """

    def __init__(self, send: Callable[..., Awaitable[Any]], *, attempts: int = 3, backoff: float = 0.01) -> None:
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        if backoff < 0:
            raise ValueError("backoff must be >= 0")
        self._send = send
        self.attempts = attempts
        self.backoff = backoff

    async def post(self, chat_id: Any, text: str) -> Any:
        """Send once, retrying transient failures; raise :class:`PostError` after
        all attempts are exhausted."""
        last_error: Optional[BaseException] = None
        for attempt in range(self.attempts):
            try:
                return await self._send(chat_id, text)
            except Exception as exc:  # transient network/API error
                last_error = exc
                if attempt < self.attempts - 1:
                    await asyncio.sleep(self.backoff)
        raise PostError(f"post to {chat_id!r} failed after {self.attempts} attempts") from last_error

    async def post_many(self, items) -> list[PostResult]:
        """Post ``(chat_id, text)`` pairs sequentially; a failure in one item
        does not stop the rest. One :class:`PostResult` per item, in order."""
        results: list[PostResult] = []
        for chat_id, text in items:
            try:
                message_id = await self.post(chat_id, text)
                results.append(PostResult(chat_id, True, message_id=message_id))
            except PostError as exc:
                results.append(PostResult(chat_id, False, error=str(exc)))
        return results
