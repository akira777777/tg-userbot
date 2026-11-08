from __future__ import annotations

import pytest

from control.config import ConfigurationError, ControlSettings


def test_settings_require_https_owner_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("MINI_APP_URL", "https://example.test/app")
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "123, 456")
    settings = ControlSettings.from_environment()
    assert settings.owner_telegram_ids == frozenset({123, 456})

    monkeypatch.setenv("MINI_APP_URL", "http://example.test")
    with pytest.raises(ConfigurationError, match="HTTPS"):
        ControlSettings.from_environment()
