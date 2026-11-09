"""CLI точки входа для контрольной плоскости Mini App.

Режимы:
  python -m control main telegram-bot   — Bot API контроллер (/start -> WebApp)
  python -m control main worker         — локальный воркер очереди команд
  python -m control main healthcheck    — проверка работоспособности
"""

from __future__ import annotations

import sys

from control.config import ControlSettings
from control.controller_bot import main as bot_main
from control.worker import main as worker_main


def healthcheck() -> int:
    try:
        settings = ControlSettings.from_environment()
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    else:
        print("OK", settings.model_redacted())
        return 0


def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python -m control main <telegram-bot|worker|healthcheck>")
        sys.exit(2)

    mode = sys.argv[1]
    if mode == "telegram-bot":
        bot_main()
    elif mode == "worker":
        worker_main()
    elif mode == "healthcheck":
        sys.exit(healthcheck())
    else:
        print(f"Неизвестный режим: {mode}")
        sys.exit(2)


if __name__ == "__main__":
    main()
