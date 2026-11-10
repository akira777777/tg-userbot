"""Локальный воркер команд: забирает задачи из Supabase, выполняет через userbot."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from control.account_alias import AccountAliasMapper
from control.cli_argv import slot_key_for_kind
from control.config import ControlSettings
from control.executor import UserbotRuntime
from control.models import CommandKind, CommandRecord, CommandStatus, TelegramIdentity
from control.policy import CommandPolicy, Redactor
from control.privacy_forward import ForwardPrivacyManager
from control.queue import QueueRepository

log = logging.getLogger("control.worker")


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
        self._lease_renew_interval = max(1.0, settings.lease_seconds / 3)

    async def run(self) -> None:
        self._running = True
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except (NotImplementedError, OSError):
                pass  # Windows may not support all signals

        log.info(
            self._safe(
                f"Контрольный воркер запущен: worker={self._settings.worker_id}, "
                f"poll={self._settings.poll_interval_seconds}s, "
                f"lease={self._settings.lease_seconds}s"
            )
        )

        while self._running:
            claimed_count = 0
            try:
                claimed_count = await self._tick()
            except Exception as exc:  # noqa: BLE001 - worker must not die on transient error
                log.error(self._safe(f"Ошибка тика воркера: {exc}"))
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
        groups: dict[str, list[CommandRecord]] = defaultdict(list)
        for record in claimed:
            groups[slot_key_for_kind(record.request.kind, record.request.account)].append(record)
        cap = max(1, min(self._settings.max_slot_concurrency, self._settings.claim_batch_size, len(groups)))
        sem = asyncio.Semaphore(cap)

        async def run_slot(records: list[CommandRecord]) -> None:
            async with sem:
                for item in records:
                    await self._process(item)

        await asyncio.gather(*(run_slot(items) for items in groups.values()))
        return len(claimed)

    async def _process(self, record: CommandRecord) -> None:
        renewed = self._queue.renew_lease(
            command_id=record.id,
            worker_id=self._settings.worker_id,
            version=record.version,
            lease_seconds=self._settings.lease_seconds,
        )
        if renewed is None:
            return

        identity = TelegramIdentity(
            telegram_user_id=record.requested_by,
            auth_date=datetime.now(UTC),
        )
        decision = self._policy.validate_request(identity, record.request)
        if not decision["ok"]:
            self._finish_rejected(record, decision["error"])
            return
        await self._maybe_stop(record)

        stop = asyncio.Event()
        lease = asyncio.create_task(self._keep_lease(record, renewed.version, stop))
        try:
            result = await self._runtime.run_command(record)
        finally:
            stop.set()
            version = await lease
        if version is None:
            return
        self._finish_result(record, result)

    async def _maybe_stop(self, record: CommandRecord) -> None:
        command_id = getattr(record.request.payload, "command_id", None)
        if record.request.kind != CommandKind.STOP or not command_id:
            return
        self._queue.cancel(command_id, self._settings.worker_id)
        terminate = getattr(self._runtime, "terminate_command", None)
        if terminate is not None:
            await terminate(command_id)

    def _finish_result(self, record: CommandRecord, result: dict[str, Any]) -> None:
        account_alias = result.get("redacted_account", "<account>")
        summary = {
            "ok": bool(result.get("ok")),
            "account": account_alias,
            "output": str(result.get("output") or result.get("error") or "")[:500],
        }
        if result.get("ok"):
            self._queue.complete(
                command_id=record.id,
                status=CommandStatus.SUCCEEDED,
                result_summary=summary,
            )
            self._emit(record.id, 2, "info", "completed", f"команда выполнена ({account_alias})")
            return
        self._queue.fail(
            command_id=record.id,
            error_code=str(result.get("error", "unknown"))[:64],
            result_summary=summary,
        )
        self._emit(record.id, 2, "error", "failed", f"ошибка выполнения ({account_alias})")

    def _finish_rejected(self, record: CommandRecord, error: str) -> None:
        summary = {"ok": False, "error": error}
        self._queue.fail(command_id=record.id, error_code="policy", result_summary=summary)
        self._emit(record.id, 2, "error", "rejected", error[:200])

    def _emit(self, command_id: UUID, sequence: int, level: str, event_type: str, safe_message: str) -> None:
        ok = self._queue.append_event(
            command_id=command_id,
            sequence=sequence,
            level=level,
            event_type=event_type,
            safe_message=safe_message,
        )
        if not ok:
            log.warning(self._safe(f"не удалось записать событие {event_type} для {command_id}"))

    def _safe(self, text: str) -> str:
        return self._forward_privacy.redact_log_line(Redactor.redact(text))

    async def _keep_lease(self, record: CommandRecord, version: int, stop: asyncio.Event) -> int | None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._lease_renew_interval)
            except TimeoutError:
                renewed = self._queue.renew_lease(
                    command_id=record.id,
                    worker_id=self._settings.worker_id,
                    version=version,
                    lease_seconds=self._settings.lease_seconds,
                )
                if renewed is None:
                    terminate = getattr(self._runtime, "terminate_command", None)
                    if terminate is not None:
                        await terminate(record.id)
                    return None
                version = renewed.version
        return version


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ControlSettings.from_environment()
    worker = ControlWorker(settings)
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        log.info("воркер остановлен")


if __name__ == "__main__":
    main()
