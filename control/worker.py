"""Локальный воркер команд: забирает задачи из Supabase, выполняет через userbot."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

from control.account_alias import AccountAliasMapper
from control.config import ControlSettings
from control.executor import UserbotRuntime
from control.models import CommandRecord, CommandStatus
from control.policy import CommandPolicy
from control.queue import QueueRepository
from control.privacy_forward import ForwardPrivacyManager


class ControlWorker:
    def __init__(self, settings: ControlSettings) -> None:
        self._settings = settings
        self._queue = QueueRepository(settings)
        self._runtime = UserbotRuntime(
            settings, Path(__file__).resolve().parent.parent
        )
        self._policy = CommandPolicy(settings)
        self._forward_privacy = ForwardPrivacyManager(enabled=settings.forward_privacy)
        self._alias_mapper = AccountAliasMapper()
        self._running = False
        self._current_lease: dict[str, Any] = {}

    async def run(self) -> None:
        self._running = True
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except (NotImplementedError, OSError):
                pass  # Windows may not support all signals

        print(
            f"Контрольный воркер запущен: worker={self._settings.worker_id}, "
            f"poll={self._settings.poll_interval_seconds}s, "
            f"lease={self._settings.lease_seconds}s"
        )

        while self._running:
            claimed_count = 0
            try:
                claimed_count = await self._tick()
            except Exception as exc:  # noqa: BLE001 - worker must not die on transient error
                print(f"Ошибка тика воркера: {self._forward_privacy.redact_log_line(str(exc))}")
            if not self._running:
                break
            if claimed_count < self._settings.claim_batch_size:
                await asyncio.sleep(self._settings.poll_interval_seconds)

    def _shutdown(self) -> None:
        self._running = False

    async def _tick(self) -> int:
        claimed = self._queue.claim_commands(
            worker_id=self._settings.worker_id,
            batch_size=self._settings.claim_batch_size,
            lease_seconds=self._settings.lease_seconds,
        )
        if not claimed:
            return 0

        for record in claimed:
            if not self._running:
                break
            await self._process(record)
        return len(claimed)

    async def _process(self, record: CommandRecord) -> None:
        # Продлеваем аренду перед началом длительной операции.
        # Потеря аренды значит, что команду уже забрал другой воркер.
        renewed = self._queue.renew_lease(
            command_id=record.id,
            worker_id=self._settings.worker_id,
            version=record.version,
            lease_seconds=self._settings.lease_seconds,
        )
        if renewed is None:
            return

        result = await self._runtime.run_command(record)
        account_alias = result.get("redacted_account", "<account>")

        if result.get("ok"):
            self._queue.complete(
                command_id=record.id,
                status=CommandStatus.SUCCEEDED,
                result_summary=f"успешно: {result.get('output','')[:200]}",
            )
            self._queue.append_event(
                command_id=record.id,
                sequence=2,
                level="info",
                event_type="completed",
                safe_message=f"команда выполнена ({account_alias})",
            )
        else:
            self._queue.fail(
                command_id=record.id,
                error_code=result.get("error", "unknown")[:64],
                result_summary=result.get("error", "")[:200],
            )
            self._queue.append_event(
                command_id=record.id,
                sequence=2,
                level="error",
                event_type="failed",
                safe_message=f"ошибка выполнения ({account_alias})",
            )


def main() -> None:
    settings = ControlSettings.from_environment()
    worker = ControlWorker(settings)
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        print("воркер остановлен")


if __name__ == "__main__":
    main()
