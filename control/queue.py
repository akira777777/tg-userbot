"""Репозиторий очереди команд через Supabase (service_role).

Все операции атомарны, поддерживают claim/renew/complete/fail/cancel.
Никаких секретов, session-путей или user_id получателей не хранятся в очереди.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

try:
    from supabase import Client, create_client
except ImportError:  # pragma: no cover - supabase опционально на этапе разработки
    create_client = None  # type: ignore[assignment]
    Client = object  # type: ignore[misc,valid-type]

from control.config import ControlSettings
from control.models import CommandPayload, CommandRecord, CommandStatus


@dataclass(frozen=True, slots=True)
class LeaseRenewal:
    """Продление аренды без полного CommandRecord."""

    version: int
    lease_expires_at: datetime | None


class QueueRepository:
    def __init__(self, settings: ControlSettings, client: Client | None = None) -> None:
        self._settings = settings
        self._client: Client | None = client
        if (
            client is None
            and create_client is not None
            and settings.supabase_url
            and settings.supabase_service_role_key
        ):
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
            return self._record(existing.data, idempotency_key=idempotency_key, requested_by=owner_telegram_id)

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
        return self._record(rec, idempotency_key=idempotency_key, requested_by=owner_telegram_id)

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
                "p_worker_id": worker_id,
                "p_batch_size": batch_size,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()

        rows = rpc.data or []
        return [self._record(row) for row in rows]

    def renew_lease(
        self,
        command_id: UUID,
        worker_id: str,
        version: int,
        lease_seconds: int = 300,
    ) -> LeaseRenewal | None:
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
            .eq("claimed_by", worker_id)
            .select("id,status,version,lease_expires_at")
            .maybe_single()
            .execute()
        )
        if not result.data:
            return None
        rec = result.data
        lease_raw = rec.get("lease_expires_at")
        lease_at = lease_raw if isinstance(lease_raw, datetime) or lease_raw is None else datetime.fromisoformat(lease_raw)
        return LeaseRenewal(version=int(rec.get("version", version + 1)), lease_expires_at=lease_at)

    def complete(
        self,
        command_id: UUID,
        status: CommandStatus,
        result_summary: dict | None = None,
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
            .eq("status", CommandStatus.CLAIMED.value)
            .select("id,status")
            .maybe_single()
            .execute()
        )
        return bool(result.data)

    def fail(
        self,
        command_id: UUID,
        error_code: str,
        result_summary: dict | None = None,
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

        update = {"status": CommandStatus.CANCELLED.value, "finished_at": self._now().isoformat()}
        pending = (
            self._client.table("control_commands")
            .update(update)
            .eq("id", str(command_id))
            .eq("status", CommandStatus.PENDING.value)
            .execute()
        )
        if pending.data:
            return True
        if not worker_id:
            return False
        claimed = (
            self._client.table("control_commands")
            .update(update)
            .eq("id", str(command_id))
            .eq("status", CommandStatus.CLAIMED.value)
            .eq("claimed_by", worker_id)
            .execute()
        )
        return bool(claimed.data)

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
        return [self._record(row) for row in rows]

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
        return self._record(result.data)

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

    def _request_from_row(self, row: dict, idempotency_key: UUID | None = None) -> CommandPayload:
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        key = idempotency_key
        if key is None and row.get("idempotency_key"):
            key = UUID(str(row["idempotency_key"]))
        kwargs: dict = {
            "kind": row["kind"],
            "mode": row["mode"],
            "account": row.get("account") or "main",
            "payload": payload,
        }
        if key is not None:
            kwargs["idempotency_key"] = key
        nonce = row.get("confirmation_nonce")
        if nonce:
            kwargs["confirmation_nonce"] = UUID(str(nonce))
        return CommandPayload(**kwargs)

    def _record(
        self,
        row: dict,
        *,
        idempotency_key: UUID | None = None,
        requested_by: int | None = None,
    ) -> CommandRecord:
        owner = requested_by if requested_by is not None else int(row["owner_telegram_id"])
        return CommandRecord(
            id=UUID(str(row["id"])),
            request=self._request_from_row(row, idempotency_key),
            requested_by=owner,
            status=CommandStatus(row["status"]),
            created_at=row["created_at"],
            claimed_at=row.get("claimed_at"),
            finished_at=row.get("finished_at"),
            lease_expires_at=row.get("lease_expires_at"),
            result_summary=row.get("result_summary"),
            error_code=row.get("error_code"),
            version=int(row.get("version") or 0),
        )

    def _digest(self, payload: dict) -> str:
        import hashlib

        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
