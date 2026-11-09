from __future__ import annotations

import pytest

from control import main as control_main
from control.config import ConfigurationError


def test_healthcheck_ok(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("MINI_APP_URL", "https://example.test/app")
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "123")
    code = control_main.healthcheck()
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.startswith("OK")
    assert "'bot_token': 'redacted'" in captured.out


def test_healthcheck_fail(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("MINI_APP_URL", raising=False)
    monkeypatch.delenv("OWNER_TELEGRAM_IDS", raising=False)
    code = control_main.healthcheck()
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out.startswith("FAIL:")


def test_main_usage_without_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_main.sys, "argv", ["control.main"])
    with pytest.raises(SystemExit) as exited:
        control_main.main()
    assert exited.value.code == 2


def test_main_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_main.sys, "argv", ["control.main", "nope"])
    with pytest.raises(SystemExit) as exited:
        control_main.main()
    assert exited.value.code == 2


def test_main_healthcheck_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_main.sys, "argv", ["control.main", "healthcheck"])
    monkeypatch.setattr(control_main, "healthcheck", lambda: 0)
    with pytest.raises(SystemExit) as exited:
        control_main.main()
    assert exited.value.code == 0


def test_main_dispatches_bot_and_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(control_main, "bot_main", lambda: called.append("bot"))
    monkeypatch.setattr(control_main, "worker_main", lambda: called.append("worker"))

    monkeypatch.setattr(control_main.sys, "argv", ["control.main", "telegram-bot"])
    control_main.main()
    monkeypatch.setattr(control_main.sys, "argv", ["control.main", "worker"])
    control_main.main()
    assert called == ["bot", "worker"]


def test_healthcheck_wraps_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise ConfigurationError("broken")

    monkeypatch.setattr("control.main.ControlSettings.from_environment", boom)
    assert control_main.healthcheck() == 1
