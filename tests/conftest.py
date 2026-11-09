from __future__ import annotations

import pytest

from control.config import ControlSettings


@pytest.fixture
def settings() -> ControlSettings:
    return ControlSettings(
        bot_token="token",
        mini_app_url="https://example.test/app",
        owner_telegram_ids=frozenset({123}),
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="secret",
        poll_interval_seconds=0.01,
        claim_batch_size=2,
        lease_seconds=30,
        worker_id="worker-test",
    )
