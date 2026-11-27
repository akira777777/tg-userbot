from __future__ import annotations

from pathlib import Path

import pytest

from control.config import ConfigurationError, ControlSettings


def test_settings_require_https_owner_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("MINI_APP_URL", "https://example.test/app")
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "123, 456")
    monkeypatch.setenv("CONTROL_SESSION_SECRET", "0123456789abcdef0123456789abcdef")
    settings = ControlSettings.from_environment()
    assert settings.owner_telegram_ids == frozenset({123, 456})

    monkeypatch.setenv("MINI_APP_URL", "http://example.test")
    with pytest.raises(ConfigurationError, match="HTTPS"):
        ControlSettings.from_environment()

    monkeypatch.setenv("MINI_APP_URL", "https://example.test/app")
    monkeypatch.setenv("CONTROL_SESSION_SECRET", "change-me-in-production")
    with pytest.raises(ConfigurationError, match="CONTROL_SESSION_SECRET"):
        ControlSettings.from_environment()


def test_from_environment_reads_repo_env_not_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("MINI_APP_URL", raising=False)
    monkeypatch.delenv("OWNER_TELEGRAM_IDS", raising=False)
    monkeypatch.delenv("CONTROL_SESSION_SECRET", raising=False)

    decoy = tmp_path / "cwd"
    decoy.mkdir()
    (decoy / ".env").write_text(
        "BOT_TOKEN=from-cwd\n"
        "MINI_APP_URL=https://cwd.test/app\n"
        "OWNER_TELEGRAM_IDS=1\n"
        "CONTROL_SESSION_SECRET=0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    real = tmp_path / "repo.env"
    real.write_text(
        "BOT_TOKEN=from-repo\n"
        "MINI_APP_URL=https://repo.test/app\n"
        "OWNER_TELEGRAM_IDS=99\n"
        "CONTROL_SESSION_SECRET=abcdefghijklmnopqrstuvwxyz012345\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("USERBOT_ENV", str(real))
    monkeypatch.chdir(decoy)

    settings = ControlSettings.from_environment()
    assert settings.bot_token == "from-repo"
    assert settings.mini_app_url == "https://repo.test/app"
    assert settings.owner_telegram_ids == frozenset({99})
