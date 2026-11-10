"""Health check for the real SQLite and control-plane runtime."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def check_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()


def check_control_settings() -> None:
    from control.config import ConfigurationError, ControlSettings

    settings = ControlSettings.from_environment()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ConfigurationError("SUPABASE_URL and SUPABASE_SECRET_KEY are required for the worker")
    locks = Path("locks")
    locks.mkdir(parents=True, exist_ok=True)
    probe = locks / ".healthcheck"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("userbot.db"))
    parser.add_argument("--require-control", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        check_sqlite(args.db)
        if args.require_control:
            check_control_settings()
    except Exception as error:  # noqa: BLE001 - health checks must report every failure
        print(f"FAIL: {error}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
