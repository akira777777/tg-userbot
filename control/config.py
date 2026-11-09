"""Configuration for trusted Mini App controller processes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when a required control-plane setting is absent or unsafe."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _session_secret() -> str:
    value = os.environ.get("CONTROL_SESSION_SECRET", "").strip()
    if len(value) < 32 or value == "change-me-in-production":
        raise ConfigurationError("CONTROL_SESSION_SECRET must be a random value of at least 32 characters")
    return value


def _owner_ids(value: str) -> frozenset[int]:
    try:
        ids = frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise ConfigurationError("OWNER_TELEGRAM_IDS must be comma-separated integers") from error
    if not ids or any(identifier <= 0 for identifier in ids):
        raise ConfigurationError("OWNER_TELEGRAM_IDS must contain positive Telegram IDs")
    return ids


@dataclass(frozen=True, slots=True)
class ControlSettings:
    bot_token: str
    mini_app_url: str
    owner_telegram_ids: frozenset[int]
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    worker_id: str = "worker-1"
    poll_interval_seconds: float = 2.0
    lease_seconds: int = 300
    claim_batch_size: int = 5
    forward_privacy: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8741
    session_secret: str = ""
    session_ttl_minutes: int = 30

    def model_redacted(self) -> dict[str, Any]:
        hidden = "redacted"
        return {
            "bot_token": hidden,
            "mini_app_url": self.mini_app_url,
            "owner_telegram_ids_count": len(self.owner_telegram_ids),
            "supabase_url": self.supabase_url,
            "supabase_service_role_key": hidden,
            "worker_id": self.worker_id,
            "poll_interval_seconds": self.poll_interval_seconds,
            "lease_seconds": self.lease_seconds,
            "forward_privacy": self.forward_privacy,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "session_ttl_minutes": self.session_ttl_minutes,
        }

    @classmethod
    def from_environment(cls) -> ControlSettings:
        load_dotenv()
        mini_app_url = _required("MINI_APP_URL")
        parsed = urlparse(mini_app_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigurationError("MINI_APP_URL must be an absolute HTTPS URL")
        return cls(
            bot_token=_required("BOT_TOKEN"),
            mini_app_url=mini_app_url,
            owner_telegram_ids=_owner_ids(_required("OWNER_TELEGRAM_IDS")),
            supabase_url=os.environ.get("SUPABASE_URL", "").strip(),
            supabase_service_role_key=os.environ.get("SUPABASE_SECRET_KEY", "").strip(),
            worker_id=os.environ.get("WORKER_ID", "worker-1"),
            poll_interval_seconds=float(os.environ.get("POLL_INTERVAL_SECONDS", "2.0")),
            lease_seconds=int(os.environ.get("LEASE_SECONDS", "300")),
            claim_batch_size=int(os.environ.get("CLAIM_BATCH_SIZE", "5")),
            forward_privacy=os.environ.get("FORWARD_PRIVACY", "1") == "1",
            api_host=os.environ.get("API_HOST", "127.0.0.1"),
            api_port=int(os.environ.get("API_PORT", "8741")),
            session_secret=_session_secret(),
            session_ttl_minutes=int(os.environ.get("SESSION_TTL_MINUTES", "30")),
        )
