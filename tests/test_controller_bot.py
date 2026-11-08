from __future__ import annotations

from control.controller_bot import make_web_app_keyboard


def test_web_app_keyboard_has_configured_https_url() -> None:
    keyboard = make_web_app_keyboard("https://example.test/mini-app")
    button = keyboard.keyboard[0][0]
    assert button.text == "Открыть панель"
    assert button.web_app.url == "https://example.test/mini-app"
