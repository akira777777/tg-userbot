"""Pydantic v2 модели команд, событий и идентичности для Mini App."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommandKind(str, Enum):
    STATUS = "STATUS"
    GROUPS = "GROUPS"
    CAMPAIGNS = "CAMPAIGNS"
    MEDIA_LIST = "MEDIA_LIST"
    BLACKLIST_ADD = "BLACKLIST_ADD"
    BROADCAST = "BROADCAST"
    DMSEND = "DMSEND"
    CONDUCT = "CONDUCT"
    STOP = "STOP"


class CommandMode(str, Enum):
    READ = "READ"
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class CommandStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ForwardPrivacyMode(str, Enum):
    DISABLED = "disabled"
    STRIP_ATTRIBUTION = "strip"
    SEND_COPY = "send_copy"


class TelegramIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    auth_date: datetime
    query_id: str | None = None


class ReadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlacklistAddPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="вручную", max_length=256)


class BroadcastPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    media_id: int | None = None
    group_ids: list[int] = Field(default_factory=list, max_length=200)
    delay_min: float = Field(default=0.0, ge=0.0, le=3600.0)
    delay_max: float = Field(default=0.0, ge=0.0, le=3600.0)
    max_runs: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def _check_delay_order(self) -> BroadcastPayload:
        if self.delay_max > 0 and self.delay_min > self.delay_max:
            raise ValueError("delay_min не может быть больше delay_max")
        return self


class DmSendPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    media_id: int | None = None
    group_id: int | None = None
    limit: int = Field(default=20, ge=1, le=200)
    delay_min: float = Field(default=0.0, ge=0.0, le=3600.0)
    delay_max: float = Field(default=0.0, ge=0.0, le=3600.0)
    repeat_ok: bool = False

    @model_validator(mode="after")
    def _check_delay_order(self) -> DmSendPayload:
        if self.delay_max > 0 and self.delay_min > self.delay_max:
            raise ValueError("delay_min не может быть больше delay_max")
        return self


class ConductPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["broadcast", "dm"]
    text: str | None = None
    media_id: int | None = None
    group_ids: list[int] = Field(default_factory=list, max_length=200)
    group_id: int | None = None
    limit: int = Field(default=0, ge=0, le=10000)
    window_hours: float = Field(default=6.0, ge=0.1, le=72.0)
    repeat_ok: bool = False


class StopPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: UUID | None = None
    account: str | Literal["all"] | None = None


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CommandKind
    mode: CommandMode
    account: str = Field(default="main", pattern=r"^[a-z0-9_\-]{1,32}$")
    payload: ReadPayload | BlacklistAddPayload | StopPayload | BroadcastPayload | DmSendPayload | ConductPayload
    idempotency_key: UUID = Field(default_factory=uuid4)
    confirmation_nonce: UUID | None = None

    @model_validator(mode="after")
    def _confirmation_required_for_live(self) -> CommandPayload:
        if self.mode == CommandMode.LIVE and self.kind in (
            CommandKind.BROADCAST,
            CommandKind.DMSEND,
            CommandKind.CONDUCT,
        ) and not self.confirmation_nonce:
            raise ValueError("LIVE режим требует confirmation_nonce")
        return self


class CommandRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    request: CommandPayload
    requested_by: int
    status: CommandStatus
    created_at: datetime
    claimed_at: datetime | None = None
    finished_at: datetime | None = None
    lease_expires_at: datetime | None = None
    result_summary: dict | None = None
    error_code: str | None = None
    version: int = 0

    @field_validator("result_summary", mode="before")
    @classmethod
    def _typed_result(cls, value):
        if value is None or isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {"text": value}
            return parsed if isinstance(parsed, dict) else {"text": value}
        return {"text": str(value)}


class CommandEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    command_id: UUID
    sequence: int
    occurred_at: datetime
    level: Literal["info", "warn", "error"]
    event_type: str = Field(max_length=64)
    safe_message: str = Field(max_length=512)


class ConfirmationIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    nonce: UUID
    owner_telegram_id: int
    request_digest: str
    expires_at: datetime
    consumed_at: datetime | None = None
    canonical_summary: str = Field(max_length=1024)


class ValidatedCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: TelegramIdentity
    request: CommandPayload
    validated: dict
