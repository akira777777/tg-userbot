"""Ошибки исполнения: короткий код для очереди + монотонные события."""

from __future__ import annotations

from pathlib import Path

from control.config import ControlSettings
from control.executor import UserbotRuntime, classify_error
from control.models import CommandKind, CommandMode, CommandPayload, CommandRecord, CommandStatus
from control.worker import ControlWorker


def settings() -> ControlSettings:
    return ControlSettings(
        bot_token="1:test",
        mini_app_url="https://example.test",
        owner_telegram_ids=frozenset({1}),
        session_secret="x" * 40,
    )


def test_classify_error_maps_known_failures() -> None:
    assert classify_error('sqlite3.OperationalError: database is locked') == "db_locked"
    assert classify_error("The process cannot access the file 'sessions/work3.session'") == "session_locked"
    assert classify_error("FloodWaitError: A wait of 42 seconds is required") == "flood_wait"
    assert classify_error("время вышло", code=None) == "failed"
    assert classify_error("что-то упало", code=2) == "exit_2"


def test_failed_result_carries_short_error_code(tmp_path: Path) -> None:
    runtime = UserbotRuntime(settings(), tmp_path)
    result = runtime._result(1, b"", "Traceback ... sqlite3.OperationalError: database is locked".encode(), "main")
    assert result["ok"] is False
    assert result["error_code"] == "db_locked"
    assert "database is locked" in result["error"]


def test_worker_keeps_error_code_and_uses_next_sequence(tmp_path: Path) -> None:
    worker = ControlWorker.__new__(ControlWorker)  # без запуска сети/Telegram
    calls: dict = {}

    class FakeQueue:
        def fail(self, **kwargs):
            calls["fail"] = kwargs
            return True

        def complete(self, **kwargs):
            calls["complete"] = kwargs
            return True

        def append_event(self, **kwargs):
            calls["event"] = kwargs
            return True

    worker._queue = FakeQueue()  # type: ignore[attr-defined]
    worker._forward_privacy = type("P", (), {"redact_log_line": staticmethod(lambda t: t)})()  # type: ignore[attr-defined]

    record = CommandRecord(
        id=__import__("uuid").uuid4(),
        request=CommandPayload(kind=CommandKind.STATUS, mode=CommandMode.READ, account="main", payload={}),
        requested_by=1,
        status=CommandStatus.CLAIMED,
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    worker._finish_result(record, {"ok": False, "error": "boom", "error_code": "timeout", "redacted_account": "Аккаунт 1"})

    assert calls["fail"]["error_code"] == "timeout"
    assert "sequence" not in calls["event"]  # нумерацию отдаёт репозиторий
