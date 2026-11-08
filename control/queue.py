"""Репозиторий очереди команд через Supabase (service_role).

Все операции атомарны, поддерживают claim/renew/complete/fail/cancel.
Никаких секретов, session-путей или user_id получателей не хранятся в очереди.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

try:
    from supabase import Client, create_client
except ImportError:  # pragma: no cover - supabase опционально на этапе разработки
    create_client = None  # type: ignore[assignment]
    Client = object  # type: ignore[misc,valid-type]

from control.config import ControlSettings
from control.models import CommandRecord, CommandStatus


class QueueRepository:
    def __init__(self, settings: ControlSettings) -> None:
        self._settings = settings
        self._client: Client | None = None
        if create_client is not None:
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def enqueue(
        self,
        owner_telegram_id: int,
        kind: str,
        mode: str,
        account: str,
        payload: dict,
        idempotency_key: UUID,
        confirmation_nonce: UUID | None = None,
    ) -> CommandRecord | None:
        """Идемпотентно вставляет pending команду. Возвращает существующую при повторном ключе."""
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        request_digest = self._digest(payload)
        existing = (
            self._client.table("control_commands")
            .select("id,status,kind,mode,account,payload,created_at,version")
            .eq("owner_telegram_id", owner_telegram_id)
            .eq("idempotency_key", str(idempotency_key))
            .maybe_single()
            .execute()
        )
        if existing.data:
            rec = existing.data
            return CommandRecord(
                id=UUID(rec["id"]),
                request={**rec["payload"], "kind": rec["kind"], "mode": rec["mode"], "account": rec["account"]},
                requested_by=owner_telegram_id,
                status=CommandStatus(rec["status"]),
                created_at=rec["created_at"],
                version=rec.get("version", 0),
            )

        insert = {
            "id": str(uuid4()),
            "idempotency_key": str(idempotency_key),
            "owner_telegram_id": owner_telegram_id,
            "kind": kind,
            "mode": mode,
            "account": account,
            "payload": payload,
            "request_digest": request_digest,
            "status": CommandStatus.PENDING.value,
            "created_at": self._now().isoformat(),
            "version": 0,
        }
        result = self._client.table("control_commands").insert(insert).execute()
        if not result.data:
            return None
        rec = result.data[0]
        self._append_event(
            UUID(rec["id"]),
            1,
            "info",
            "queued",
            f"команда поставлена в очередь: {kind} ({mode})",
        )
        return CommandRecord(
            id=UUID(rec["id"]),
            request={**rec["payload"], "kind": rec["kind"], "mode": rec["mode"], "account": rec["account"]},
            requested_by=owner_telegram_id,
            status=CommandStatus.PENDING,
            created_at=rec["created_at"],
        )

    def claim_commands(
        self,
        worker_id: str,
        batch_size: int = 5,
        lease_seconds: int = 300,
    ) -> list[CommandRecord]:
        """Атоматически забирает pending/истёкшие команды через RPC claim_control_commands."""
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        rpc = self._client.rpc(
            "claim_control_commands",
            {
                "worker_id": worker_id,
                "batch_size": batch_size,
                "lease_seconds": lease_seconds,
            },
        ).execute()

        rows = rpc.data or []
        return [
            CommandRecord(
                id=UUID(row["id"]),
                request={
                    **row["payload"],
                    "kind": row["kind"],
                    "mode": row["mode"],
                    "account": row["account"],
                },
                requested_by=row["owner_telegram_id"],
                status=CommandStatus(row["status"]),
                created_at=row["created_at"],
                claimed_at=row.get("claimed_at"),
                lease_expires_at=row.get("lease_expires_at"),
                version=row.get("version", 0),
            )
            for row in rows
        ]

    def renew_lease(
        self,
        command_id: UUID,
        worker_id: str,
        version: int,
        lease_seconds: int = 300,
    ) -> CommandRecord | None:
        """Продлевает аренду команды. Возвращает None если команда уже кем-то другой взята."""
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        expires_at = (self._now() + timedelta(seconds=lease_seconds)).isoformat()
        result = (
            self._client.table("control_commands")
            .update(
                {
                    "claimed_by": worker_id,
                    "lease_expires_at": expires_at,
                    "version": version + 1,
                }
            )
            .eq("id", str(command_id))
            .eq("version", version)
            .eq("status", CommandStatus.CLAIMED.value)
            .select("id,status,version,lease_expires_at")
            .maybe_single()
            .execute()
        )
        if not result.data:
            return None
        rec = result.data
        return CommandRecord(
            id=UUID(rec["id"]),
            status=CommandStatus(rec["status"]),
            created_at=rec["created_at"],
            version=rec.get("version", version + 1),
            lease_expires_at=rec.get("lease_expires_at"),
            requested_by=0,  # не нужно для renouvellement
        )

    def complete(
        self,
        command_id: UUID,
        status: CommandStatus,
        result_summary: str | None = None,
        error_code: str | None = None,
    ) -> bool:
        """Завершает команду с терминальным статусом (один раз)."""
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        update = {
            "status": status.value,
            "finished_at": self._now().isoformat(),
            "result_summary": result_summary,
            "error_code": error_code,
        }
        result = (
            self._client.table("control_commands")
            .update(update)
            .eq("id", str(command_id))
            .eq("status", CommandStatus.RUNNING.value)  # только из running
            .select("id,status")
            .maybe_single()
            .execute()
        )
        return bool(result.data)

    def fail(
        self,
        command_id: UUID,
        error_code: str,
        result_summary: str | None = None,
    ) -> bool:
        return self.complete(command_id, CommandStatus.FAILED, result_summary, error_code)

    def cancel(
        self,
        command_id: UUID,
        worker_id: str | None = None,
    ) -> bool:
        """Отменяет pending/claimed команду."""
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        filters = [
            ("id", str(command_id)),
            ("status", CommandStatus.PENDING.value),
        ]
        # claimed может отменить только владелец аренды
        query = self._client.table("control_commands").update(
            {"status": CommandStatus.CANCELLED.value, "finished_at": self._now().isoformat()}
        )
        # Оптимистично: пробуем pending, затем claimed по worker_id
        result = query.eq("id", str(command_id)).eq("status", CommandStatus.PENDING.value).execute()
        if result.data:
            return True
        if worker_id:
            result = (
                query.eq("id", str(command_id))
                .eq("status", CommandStatus.CLAIMED.value)
                .eq("claimed_by", worker_id)
                .execute()
            )
            return bool(result.data)
        return False

    def get_history(
        self,
        owner_telegram_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CommandRecord]:
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        result = (
            self._client.table("control_commands")
            .select("*")
            .eq("owner_telegram_id", owner_telegram_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        rows = result.data or []
        return [
            CommandRecord(
                id=UUID(row["id"]),
                request={
                    **row["payload"],
                    "kind": row["kind"],
                    "mode": row["mode"],
                    "account": row["account"],
                },
                requested_by=row["owner_telegram_id"],
                status=CommandStatus(row["status"]),
                created_at=row["created_at"],
                claimed_at=row.get("claimed_at"),
                finished_at=row.get("finished_at"),
                result_summary=row.get("result_summary"),
                error_code=row.get("error_code"),
                version=row.get("version", 0),
            )
            for row in rows
        ]

    def get_command(self, command_id: UUID, owner_telegram_id: int) -> CommandRecord | None:
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        result = (
            self._client.table("control_commands")
            .select("*")
            .eq("id", str(command_id))
            .eq("owner_telegram_id", owner_telegram_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            return None
        row = result.data
        return CommandRecord(
            id=UUID(row["id"]),
            request={
                **row["payload"],
                "kind": row["kind"],
                "mode": row["mode"],
                "account": row["account"],
            },
            requested_by=row["owner_telegram_id"],
            status=CommandStatus(row["status"]),
            created_at=row["created_at"],
            claimed_at=row.get("claimed_at"),
            finished_at=row.get("finished_at"),
            lease_expires_at=row.get("lease_expires_at"),
            result_summary=row.get("result_summary"),
            error_code=row.get("error_code"),
            version=row.get("version", 0),
        )

    def append_event(
        self,
        command_id: UUID,
        sequence: int,
        level: str,
        event_type: str,
        safe_message: str,
    ) -> bool:
        if self._client is None:
            raise RuntimeError("Supabase client не инициализирован")

        try:
            self._client.table("control_command_events").insert(
                {
                    "command_id": str(command_id),
                    "sequence": sequence,
                    "occurred_at": self._now().isoformat(),
                    "level": level,
                    "event_type": event_type,
                    "safe_message": safe_message[:512],
                }
            ).execute()
            return True
        except Exception:
            return False

    def _append_event(
        self,
        command_id: UUID,
        sequence: int,
        level: str,
        event_type: str,
        safe_message: str,
    ) -> None:
        self.append_event(command_id, sequence, level, event_type, safe_message)

    def _digest(self, payload: dict) -> str:
        import hashlib

        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
