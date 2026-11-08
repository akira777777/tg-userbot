from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from control.telegram_auth import TelegramAuthError, verify_init_data

TOKEN = "test-token"
NOW = 1_700_000_000


def signed_init_data(**fields: str) -> str:
    values = {"auth_date": str(NOW), "query_id": "query", "user": '{"id":123,"username":"owner","first_name":"Owner"}'}
    values.update(fields)
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_verify_init_data_returns_identity() -> None:
    identity = verify_init_data(signed_init_data(), TOKEN, now=lambda: NOW)
    assert identity.telegram_user_id == 123
    assert identity.username == "owner"


@pytest.mark.parametrize(
    "init_data, message",
    [
        ("auth_date=1&user=%7B%7D", "missing initData hash"),
        (signed_init_data().replace("owner", "intruder"), "invalid initData hash"),
        (signed_init_data(auth_date=str(NOW - 301)), "expired initData"),
        (signed_init_data() + "&user=%7B%22id%22%3A123%7D", "duplicate initData field"),
    ],
)
def test_verify_init_data_rejects_untrusted_values(init_data: str, message: str) -> None:
    with pytest.raises(TelegramAuthError, match=message):
        verify_init_data(init_data, TOKEN, now=lambda: NOW)
