"""Poster: single send, retry on transient failure, per-item results for batches."""

from __future__ import annotations

import pytest

from src.poster import PostError, PostResult, Poster


async def test_post_calls_sender_once_on_success() -> None:
    sent: list[tuple[int, str]] = []

    async def send(chat_id: int, text: str) -> str:
        sent.append((chat_id, text))
        return f"msg-{chat_id}"

    poster = Poster(send)

    result = await poster.post(1, "hello")

    assert result == "msg-1"
    assert sent == [(1, "hello")]


async def test_post_retries_transient_failures_then_succeeds() -> None:
    calls = 0

    async def flaky_send(chat_id: int, text: str) -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("temporary outage")
        return "ok"

    poster = Poster(flaky_send, attempts=3, backoff=0.001)

    result = await poster.post(7, "x")

    assert result == "ok"
    assert calls == 3


async def test_post_raises_after_attempts_exhausted() -> None:
    calls = 0

    async def always_fail(chat_id: int, text: str) -> str:
        nonlocal calls
        calls += 1
        raise ConnectionError("hard down")

    poster = Poster(always_fail, attempts=4, backoff=0)

    with pytest.raises(PostError):
        await poster.post(1, "x")

    assert calls == 4


async def test_post_retries_exhausted_error_mentions_target() -> None:
    async def always_fail(chat_id: int, text: str) -> str:
        raise RuntimeError("nope")

    poster = Poster(always_fail, attempts=2, backoff=0)

    with pytest.raises(PostError, match="42"):
        await poster.post(42, "x")


async def test_post_many_continues_after_item_failure() -> None:
    sent: list[int] = []

    async def send(chat_id: int, text: str) -> str:
        if chat_id == 1:
            raise ConnectionError("recipient down")
        sent.append(chat_id)
        return f"ok-{chat_id}"

    results = await Poster(send, backoff=0).post_many([(1, "a"), (2, "b"), (3, "c")])

    assert len(results) == 3
    assert [r.ok for r in results] == [False, True, True]
    assert results[0].chat_id == 1
    assert results[0].error
    assert results[0].message_id is None
    assert results[1].message_id == "ok-2"
    assert sent == [2, 3]


@pytest.mark.parametrize("attempts", [0, -1])
def test_invalid_attempts_rejected(attempts: int) -> None:
    with pytest.raises(ValueError):
        Poster(lambda: None, attempts=attempts)


def test_negative_backoff_rejected() -> None:
    with pytest.raises(ValueError):
        Poster(lambda: None, backoff=-0.1)


def test_post_result_shape() -> None:
    result = PostResult(chat_id=5, ok=True, message_id="m-5")
    assert result.chat_id == 5
    assert result.ok is True
    assert result.message_id == "m-5"
    assert result.error is None
