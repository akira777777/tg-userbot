"""Telegram Web App initData verification.

The browser-delivered payload is untrusted until its Telegram HMAC is verified
using the Bot API token held only by a trusted server.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl


class TelegramAuthError(ValueError):
    """Raised for invalid, stale, or malformed Telegram Web App identity data."""


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_user_id: int
    username: str | None
    first_name: str | None
    auth_date: int
    query_id: str | None


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 300,
    now: Callable[[], float] = time.time,
) -> TelegramIdentity:
    """Verify Telegram Mini App initData and return its safe identity fields."""
    if not init_data or not bot_token:
        raise TelegramAuthError("missing authentication data")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    values: dict[str, str] = {}
    received_hash: str | None = None
    for key, value in pairs:
        if key in values or (key == "hash" and received_hash is not None):
            raise TelegramAuthError("duplicate initData field")
        if key == "hash":
            received_hash = value
        else:
            values[key] = value
    if not received_hash:
        raise TelegramAuthError("missing initData hash")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramAuthError("invalid initData hash")
    try:
        auth_date = int(values["auth_date"])
    except (KeyError, ValueError) as error:
        raise TelegramAuthError("invalid auth_date") from error
    current = int(now())
    if auth_date > current + 30 or current - auth_date > max_age_seconds:
        raise TelegramAuthError("expired initData")
    try:
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise TelegramAuthError("invalid Telegram user") from error
    if user_id <= 0:
        raise TelegramAuthError("invalid Telegram user")
    return TelegramIdentity(
        telegram_user_id=user_id,
        username=user.get("username") or None,
        first_name=user.get("first_name") or None,
        auth_date=auth_date,
        query_id=values.get("query_id") or None,
    )
