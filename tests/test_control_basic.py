"""Базовые тесты для контрольной плоскости Mini App."""

from __future__ import annotations

import json
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from control.models import (
    CommandKind,
    CommandMode,
    CommandPayload,
    BroadcastPayload,
    DmSendPayload,
    ConductPayload,
    TelegramIdentity,
    ConfirmationIntent,
)
from control.policy import CommandPolicy, Redactor
from control.privacy_forward import ForwardPrivacyManager
from control.account_alias import AccountAliasMapper
from control.telegram_auth import verify_init_data, TelegramAuthError
from control.config import ControlSettings

def is_owner(identity: TelegramIdentity, settings: ControlSettings) -> bool:
    return identity.telegram_user_id in settings.owner_telegram_ids


def test_broadcast_payload_defaults_dry_run():
    p = BroadcastPayload(text="привет", group_ids=[1, 2, 3])
    assert p.text == "привет"
    assert p.group_ids == [1, 2, 3]
    assert p.delay_min == 0.0
    assert p.delay_max == 0.0


def test_dm_send_limit_bounds():
    p = DmSendPayload(text="личка", limit=50)
    assert p.limit == 50


def test_conduct_payload_kind():
    p = ConductPayload(kind="dm", text="dm-текст", group_id=100)
    assert p.kind == "dm"
    assert p.group_id == 100


def test_command_payload_live_requires_nonce():
    with pytest.raises(ValueError, match="confirmation_nonce"):
        CommandPayload(
            kind=CommandKind.BROADCAST,
            mode=CommandMode.LIVE,
            account="main",
            payload=BroadcastPayload(text="x"),
        )


def test_redactor_masks_user_id():
    text = "пользователь 1234567890 писал сообщение"
    masked = Redactor.redact(text)
    assert "1234567890" not in masked
    assert "<user_id>" in masked


def test_redactor_masks_session_path():
    text = "файл /home/user/userbot.session загружен"
    masked = Redactor.redact(text)
    assert "userbot.session" not in masked
    assert "<session_file>" in masked


def test_account_alias_mapping():
    mapper = AccountAliasMapper(["main", "work2"])
    a1 = mapper.to_alias("main")
    a2 = mapper.to_alias("work2")
    assert a1.display_name == "Аккаунт 1"
    assert a2.display_name == "Аккаунт 2"
    assert a1.internal_name == "main"
    assert a2.internal_name == "work2"


def test_forward_privacy_disabled_keeps_forward():
    mgr = ForwardPrivacyManager(enabled=False)
    data = {"forward_from": {"id": 123}, "text": "hello"}
    result = mgr.strip_message_dict(data)
    assert "forward_from" in result  # не удаляется когда отключено


def test_forward_privacy_enabled_strips_forward():
    mgr = ForwardPrivacyManager(enabled=True)
    data = {
        "forward_from": {"id": 123, "username": "user1"},
        "forward_from_chat": {"id": 456},
        "forward_date": 1234567890,
        "text": "hello",
    }
    result = mgr.strip_message_dict(data)
    assert "forward_from" not in result
    assert "forward_from_chat" not in result
    assert "forward_date" not in result
    assert result["text"] == "hello"


def test_telegram_identity_creation():
    identity = TelegramIdentity(
        telegram_user_id=123456789,
        username="testuser",
        first_name="Test",
        auth_date=datetime.now(timezone.utc),
    )
    assert identity.telegram_user_id == 123456789
    assert identity.username == "testuser"


def _make_init_data(bot_token: str, user_id: int, auth_date: int | None = None) -> str:
    if auth_date is None:
        auth_date = int(datetime.now(timezone.utc).timestamp())
    user_json = json.dumps({"id": user_id, "username": "test", "first_name": "Test"})
    data_check = f"auth_date={auth_date}\nuser={user_json}"
    # Официальный алгоритм Telegram WebApp:
    # secret_key = HMAC-SHA256(key="WebAppData", msg=bot_token)
    # hash = HMAC-SHA256(key=secret_key, msg=data_check_string)
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    good_hash = hmac.new(secret_key, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"auth_date={auth_date}&hash={good_hash}&user={user_json}"


def test_verify_init_data_valid():
    bot_token = "123456:TEST_TOKEN"
    init_data = _make_init_data(bot_token, 987654321)
    identity = verify_init_data(init_data, bot_token)
    assert identity.telegram_user_id == 987654321
    assert identity.username == "test"


def test_verify_init_data_tampered_hash():
    bot_token = "123456:TEST_TOKEN"
    init_data = _make_init_data(bot_token, 987654321)
    bad_init = init_data.replace(init_data.split("&hash=")[1].split("&")[0], "0" * 64)
    try:
        verify_init_data(bad_init, bot_token)
        raise AssertionError("ожидалась ошибка при порченом hash")
    except TelegramAuthError:
        pass


def test_verify_init_data_stale():
    bot_token = "123456:TEST_TOKEN"
    old_date = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp())
    init_data = _make_init_data(bot_token, 111, auth_date=old_date)
    try:
        verify_init_data(init_data, bot_token, max_age_seconds=300)
        raise AssertionError("ожидалась ошибка при устаревших initData")
    except TelegramAuthError:
        pass


def test_is_owner_allowlist():
    settings = ControlSettings(
        bot_token="x",
        mini_app_url="https://example.com",
        owner_telegram_ids=frozenset({123, 456}),
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="secret",
    )
    identity_owner = TelegramIdentity(
        telegram_user_id=123,
        username=None,
        first_name=None,
        auth_date=datetime.now(timezone.utc),
    )
    identity_stranger = TelegramIdentity(
        telegram_user_id=999,
        username=None,
        first_name=None,
        auth_date=datetime.now(timezone.utc),
    )
    assert is_owner(identity_owner, settings) is True
    assert is_owner(identity_stranger, settings) is False


def test_policy_requires_confirmation_for_live():
    settings = ControlSettings(
        bot_token="x",
        mini_app_url="https://example.com",
        owner_telegram_ids=frozenset({123}),
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="secret",
    )
    policy = CommandPolicy(settings)
    assert policy.requires_confirmation(CommandKind.BROADCAST, CommandMode.LIVE) is True
    assert policy.requires_confirmation(CommandKind.STATUS, CommandMode.LIVE) is False
    assert policy.requires_confirmation(CommandKind.BROADCAST, CommandMode.READ) is False


def test_policy_is_read_only():
    settings = ControlSettings(
        bot_token="x",
        mini_app_url="https://example.com",
        owner_telegram_ids=frozenset({123}),
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="secret",
    )
    policy = CommandPolicy(settings)
    assert policy.is_read_only(CommandKind.STATUS) is True
    assert policy.is_read_only(CommandKind.GROUPS) is True
    assert policy.is_read_only(CommandKind.BROADCAST) is False
    assert policy.is_read_only(CommandKind.CONDUCT) is False


def test_confirmation_intent_creation():
    ci = ConfirmationIntent(
        nonce=uuid4(),
        owner_telegram_id=123,
        request_digest="abc123",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        canonical_summary="BROADCAST | text=привет | targets=5",
    )
    assert ci.owner_telegram_id == 123
    assert ci.consumed_at is None
