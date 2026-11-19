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
from control.models import CommandKind, CommandMode, CommandRecord, CommandStatus, TelegramIdentity
from control.policy import LIVE_REQUIRES_CONFIRMATION, CommandPolicy, Redactor
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
        self._slot_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._inflight: set[asyncio.Task[None]] = set()
        self._sem: asyncio.Semaphore | None = None

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
        self._send_heartbeat()

        try:
            while self._running:
                claimed_count = 0
                try:
                    claimed_count = await self._tick()
                except Exception as exc:  # noqa: BLE001 - worker must not die on transient error
                    log.error(self._safe(f"Ошибка тика воркера: {exc}"))
                if not self._running:
                    break
                self._send_heartbeat()
                if claimed_count < self._settings.claim_batch_size:
                    await asyncio.sleep(self._settings.poll_interval_seconds)
        finally:
            self._send_heartbeat()
            await self.drain()

    async def drain(self) -> None:
        """Дождаться завершения уже запущенных фоновых задач воркера."""
        if not self._inflight:
            return
        await asyncio.gather(*list(self._inflight), return_exceptions=True)

    def _shutdown(self) -> None:
        self._running = False

    def _send_heartbeat(self) -> None:
        try:
            ok = self._queue.heartbeat(self._settings.worker_id)
            if not ok:
                log.warning(self._safe("не удалось записать сердцебиение воркера"))
        except Exception as exc:  # noqa: BLE001 - heartbeat never kills the loop
            log.warning(self._safe(f"сердцебиение воркера не отправлено: {exc}"))

    def _track(self, task: asyncio.Task[None]) -> None:
        self._inflight.add(task)

        def _done(done_task: asyncio.Task[None]) -> None:
            self._inflight.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                log.error(self._safe(f"Ошибка фоновой задачи воркера: {exc}"))

        task.add_done_callback(_done)

    async def _tick(self) -> int:
        claimed = await asyncio.to_thread(
            self._queue.claim_commands,
            worker_id=self._settings.worker_id,
            batch_size=self._settings.claim_batch_size,
            lease_seconds=self._settings.lease_seconds,
        )
        if not claimed:
            return 0
        if self._sem is None:
            cap = max(1, min(self._settings.max_slot_concurrency, self._settings.claim_batch_size))
            self._sem = asyncio.Semaphore(cap)

        groups: dict[str, list[CommandRecord]] = defaultdict(list)
        immediate_stops: list[CommandRecord] = []
        for record in claimed:
            if record.request.kind == CommandKind.STOP:
                immediate_stops.append(record)
            else:
                groups[slot_key_for_kind(record.request.kind, record.request.account)].append(record)

        # STOP никогда не ждёт занятый слот или семафор — выполняется немедленно.
        for stop_record in immediate_stops:
            self._track(asyncio.create_task(self._process(stop_record)))

        async def run_slot(slot_key: str, records: list[CommandRecord]) -> None:
            lock = self._slot_locks[slot_key]
            assert self._sem is not None
            async with lock:
                async with self._sem:
                    for item in records:
                        await self._process(item)

        for slot_key, items in groups.items():
            self._track(asyncio.create_task(run_slot(slot_key, items)))

        # Даём запущенным задачам шагнуть в цикл событий, но не блокируем следующий poll тик.
        await asyncio.sleep(0)
        return len(claimed)

    async def _process(self, record: CommandRecord) -> None:
        # Защита в глубину: повторно забранная после истечения аренды LIVE-команда
        # переводится в EXPIRED вместо автоматического повторного запуска.
        if (
            record.request.mode == CommandMode.LIVE
            and record.request.kind in LIVE_REQUIRES_CONFIRMATION
            and record.version > 1
        ):
            await asyncio.to_thread(self._finish_expired, record)
            return

        renewed = await asyncio.to_thread(
            self._queue.renew_lease,
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
            await asyncio.to_thread(self._finish_rejected, record, decision["error"])
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
        await asyncio.to_thread(self._finish_result, record, result)

    async def _maybe_stop(self, record: CommandRecord) -> None:
        command_id = getattr(record.request.payload, "command_id", None)
        if record.request.kind != CommandKind.STOP or not command_id:
            return
        await asyncio.to_thread(self._queue.cancel, command_id, self._settings.worker_id)
        terminate = getattr(self._runtime, "terminate_command", None)
        if terminate is not None:
            await terminate(command_id)

    def _finish_expired(self, record: CommandRecord) -> None:
        summary = {"ok": False, "error": "аренда LIVE-команды истекла"}
        self._queue.complete(
            command_id=record.id,
            status=CommandStatus.EXPIRED,
            result_summary=summary,
            error_code="expired",
        )
        self._emit(record.id, "warn", "expired", "LIVE-команда переведена в EXPIRED после истечения аренды")

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
            self._emit(record.id, "info", "completed", f"команда выполнена ({account_alias})")
            return
        self._queue.fail(
            command_id=record.id,
            # Короткий стабильный код; текст ошибки остаётся в result_summary.output.
            error_code=str(result.get("error_code") or "failed")[:32],
            result_summary=summary,
        )
        self._emit(record.id, "error", "failed", f"ошибка выполнения ({account_alias})")

    def _finish_rejected(self, record: CommandRecord, error: str) -> None:
        summary = {"ok": False, "error": error}
        self._queue.fail(command_id=record.id, error_code="policy", result_summary=summary)
        self._emit(record.id, "error", "rejected", error[:200])

    def _emit(self, command_id: UUID, level: str, event_type: str, safe_message: str) -> None:
        # sequence выделяет репозиторий (следующий свободный): фиксированная
        # двойка конфликтовала с событием "queued" и события терялись.
        ok = self._queue.append_event(
            command_id=command_id,
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
                renewed = await asyncio.to_thread(
                    self._queue.renew_lease,
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
