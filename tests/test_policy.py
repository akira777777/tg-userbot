from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control.config import ControlSettings
from control.models import (
    BroadcastPayload,
    CommandKind,
    CommandMode,
    CommandPayload,
    ConductPayload,
    DmSendPayload,
    ReadPayload,
    TelegramIdentity,
)
from control.policy import MAX_DM_LIMIT, MAX_GROUP_IDS, MAX_TEXT_LENGTH, CommandPolicy, Redactor


def _identity(user_id: int = 123) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=user_id,
        username="owner",
        first_name="Owner",
        auth_date=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_policy_rejects_non_owner(settings: ControlSettings) -> None:
    policy = CommandPolicy(settings)
    request = CommandPayload(kind=CommandKind.STATUS, mode=CommandMode.READ, payload=ReadPayload())
    result = policy.validate_request(_identity(999), request)
    assert result == {"ok": False, "error": "недостаточно прав"}


def test_policy_requires_live_confirmation_nonce(settings: ControlSettings) -> None:
    policy = CommandPolicy(settings)
    request = CommandPayload.model_construct(
        kind=CommandKind.BROADCAST,
        mode=CommandMode.LIVE,
        account="main",
        payload=BroadcastPayload(text="x"),
        idempotency_key=uuid4(),
        confirmation_nonce=None,
    )
    result = policy.validate_request(_identity(), request)
    assert result["ok"] is False
    assert "confirmation_nonce" in result["error"]


@pytest.mark.parametrize(
    ("kind", "payload", "error"),
    [
        (
            CommandKind.BROADCAST,
            BroadcastPayload.model_construct(
                text="ok",
                media_id=None,
                group_ids=list(range(MAX_GROUP_IDS + 1)),
                delay_min=0.0,
                delay_max=0.0,
                max_runs=0,
            ),
            "слишком много group_ids",
        ),
        (
            CommandKind.BROADCAST,
            BroadcastPayload(text="x" * (MAX_TEXT_LENGTH + 1), group_ids=[1]),
            "текст слишком длинный",
        ),
        (
            CommandKind.DMSEND,
            DmSendPayload.model_construct(
                text="ok",
                media_id=None,
                group_id=None,
                limit=MAX_DM_LIMIT + 1,
                delay_min=0.0,
                delay_max=0.0,
                repeat_ok=False,
            ),
            "превышен лимит получателей DM",
        ),
        (
            CommandKind.DMSEND,
            DmSendPayload(text="y" * (MAX_TEXT_LENGTH + 1), limit=1),
            "текст слишком длинный",
        ),
        (
            CommandKind.CONDUCT,
            ConductPayload.model_construct(
                kind="dm",
                text="ok",
                media_id=None,
                group_ids=list(range(MAX_GROUP_IDS + 1)),
                group_id=None,
                limit=0,
                window_hours=6.0,
                repeat_ok=False,
            ),
            "слишком много group_ids",
        ),
        (
            CommandKind.CONDUCT,
            ConductPayload(kind="broadcast", text="z" * (MAX_TEXT_LENGTH + 1)),
            "текст слишком длинный",
        ),
    ],
)
def test_policy_payload_limits(
    settings: ControlSettings,
    kind: CommandKind,
    payload: BroadcastPayload | DmSendPayload | ConductPayload,
    error: str,
) -> None:
    policy = CommandPolicy(settings)
    request = CommandPayload.model_construct(
        kind=kind,
        mode=CommandMode.DRY_RUN,
        account="main",
        payload=payload,
        idempotency_key=uuid4(),
        confirmation_nonce=None,
    )
    result = policy.validate_request(_identity(), request)
    assert result == {"ok": False, "error": error}


def test_policy_requires_idempotency_key(settings: ControlSettings) -> None:
    policy = CommandPolicy(settings)
    request = CommandPayload.model_construct(
        kind=CommandKind.STATUS,
        mode=CommandMode.READ,
        account="main",
        payload=ReadPayload(),
        idempotency_key=None,
        confirmation_nonce=None,
    )
    result = policy.validate_request(_identity(), request)
    assert result == {"ok": False, "error": "отсутствует idempotency_key"}


def test_policy_accepts_valid_dry_run(settings: ControlSettings) -> None:
    policy = CommandPolicy(settings)
    key = uuid4()
    request = CommandPayload(
        kind=CommandKind.BROADCAST,
        mode=CommandMode.DRY_RUN,
        payload=BroadcastPayload(text="hello", group_ids=[1]),
        idempotency_key=key,
    )
    result = policy.validate_request(_identity(), request, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert result["ok"] is True
    assert result["validated"]["kind"] == CommandKind.BROADCAST
    assert result["validated"]["idempotency_key"] == str(key)
    assert result["validated"]["requested_by"] == 123
    assert result["validated"]["forward_privacy"] is True


def test_redactor_and_aliases() -> None:
    text = "API_ID_main = 12345 API_HASH_main = abc sb_secret_xyz /data/acc.session token" + ("A" * 40)
    redacted = Redactor.redact(text)
    assert "API_ID=***" in redacted
    assert "API_HASH=***" in redacted
    assert "sb_secret=***" in redacted
    assert "<session_file>" in redacted
    assert Redactor.redact_account_name("main") == "Аккаунт 1"
    assert Redactor.redact_account_name("work2") == "Аккаунт 3"
    assert Redactor.redact_account_name("acc0") == "Аккаунт 1"
    assert Redactor.redact_account_name("unknown") == "<account>"
    assert CommandPolicy.account_alias("main") == "Аккаунт 1"
    assert CommandPolicy.redact_text("id 999999999") == "id <user_id>"


def test_safe_summary_and_flags(settings: ControlSettings) -> None:
    policy = CommandPolicy(settings)
    summary = policy.safe_summary(
        CommandKind.BROADCAST,
        {"text": "abcdefghij" * 10, "group_ids": [1, 2], "limit": 4},
    )
    assert summary.startswith("BROADCAST")
    assert "targets=2" in summary
    assert "limit=4" in summary
    assert policy.is_read_only(CommandKind.CAMPAIGNS) is True
    assert policy.requires_confirmation(CommandKind.DMSEND, CommandMode.LIVE) is True
