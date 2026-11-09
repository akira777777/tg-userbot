"""Очередь команд: claim, аренда, одно завершение и идемпотентность."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from control.config import ControlSettings
from control.models import CommandKind, CommandStatus
from control.queue import LeaseRenewal, QueueRepository


def _settings() -> ControlSettings:
    return ControlSettings(
        bot_token="token",
        mini_app_url="https://example.test/app",
        owner_telegram_ids=frozenset({1}),
    )


class _Result:
    def __init__(self, data) -> None:
        self.data = data


class _Query:
    def __init__(self, db: FakeSupabase, table: str, action: str, body: dict | None = None) -> None:
        self.db = db
        self.table = table
        self.action = action
        self.body = body
        self.filters: dict = {}
        self.single = False

    def select(self, _columns: str) -> _Query:
        return self

    def eq(self, key: str, value) -> _Query:
        self.filters[key] = value
        return self

    def maybe_single(self) -> _Query:
        self.single = True
        return self

    def order(self, *_args, **_kwargs) -> _Query:
        return self

    def range(self, *_args) -> _Query:
        return self

    def execute(self) -> _Result:
        return self.db.execute(self)


class _Table:
    def __init__(self, db: FakeSupabase, name: str) -> None:
        self.db = db
        self.name = name

    def select(self, columns: str) -> _Query:
        return _Query(self.db, self.name, "select").select(columns)

    def insert(self, row: dict) -> _Query:
        return _Query(self.db, self.name, "insert", row)

    def update(self, row: dict) -> _Query:
        return _Query(self.db, self.name, "update", row)


class FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "control_commands": [],
            "control_command_events": [],
        }
        self.rpc_calls: list[tuple[str, dict]] = []
        self.claim_rows: list[dict] = []
        self.updates: list[dict] = []

    def table(self, name: str) -> _Table:
        return _Table(self, name)

    def rpc(self, name: str, args: dict) -> _Query:
        self.rpc_calls.append((name, dict(args)))
        return _Query(self, name, "rpc", args)

    def execute(self, query: _Query) -> _Result:
        if query.action == "rpc":
            return _Result(list(self.claim_rows))
        rows = self.tables.setdefault(query.table, [])
        if query.action == "insert":
            if query.table == "control_command_events":
                for existing in rows:
                    if (
                        existing["command_id"] == query.body["command_id"]
                        and existing["sequence"] == query.body["sequence"]
                    ):
                        raise RuntimeError("duplicate sequence")
            stored = dict(query.body)
            rows.append(stored)
            return _Result([stored])
        matched = [row for row in rows if all(row.get(key) == value for key, value in query.filters.items())]
        if query.action == "update":
            self.updates.append(
                {"table": query.table, "body": dict(query.body or {}), "filters": dict(query.filters)}
            )
            updated = []
            for row in matched:
                row.update(query.body or {})
                updated.append(dict(row))
            if query.single:
                return _Result(updated[0] if updated else None)
            return _Result(updated)
        copied = [dict(row) for row in matched]
        if query.single:
            return _Result(copied[0] if copied else None)
        return _Result(copied)


def _repo() -> tuple[QueueRepository, FakeSupabase]:
    fake = FakeSupabase()
    return QueueRepository(_settings(), client=fake), fake


def _seed_claimed(fake: FakeSupabase, command_id, *, version: int = 1, claimed_by: str = "worker-a") -> None:
    now = datetime.now(UTC).isoformat()
    fake.tables["control_commands"].append(
        {
            "id": str(command_id),
            "idempotency_key": str(uuid4()),
            "owner_telegram_id": 1,
            "kind": "STATUS",
            "mode": "READ",
            "account": "main",
            "payload": {},
            "status": "CLAIMED",
            "created_at": now,
            "version": version,
            "claimed_by": claimed_by,
        }
    )


def test_claim_uses_sql_argument_names_and_nested_payload():
    repo, fake = _repo()
    command_id = uuid4()
    created = datetime.now(UTC).isoformat()
    fake.claim_rows = [
        {
            "id": str(command_id),
            "idempotency_key": str(uuid4()),
            "owner_telegram_id": 7,
            "kind": "BROADCAST",
            "mode": "DRY_RUN",
            "account": "main",
            "payload": {"text": "привет", "group_ids": [1, 2]},
            "status": "CLAIMED",
            "created_at": created,
            "claimed_at": created,
            "lease_expires_at": created,
            "version": 1,
        }
    ]
    claimed = repo.claim_commands("worker-a", batch_size=5, lease_seconds=300)
    assert fake.rpc_calls == [
        (
            "claim_control_commands",
            {"p_worker_id": "worker-a", "p_batch_size": 5, "p_lease_seconds": 300},
        )
    ]
    assert len(claimed) == 1
    assert claimed[0].id == command_id
    assert claimed[0].request.kind == CommandKind.BROADCAST
    assert claimed[0].request.account == "main"
    assert claimed[0].request.payload.text == "привет"
    assert claimed[0].status.value == "CLAIMED"


def test_complete_and_fail_from_claimed_once():
    repo, fake = _repo()
    done_id = uuid4()
    failed_id = uuid4()
    _seed_claimed(fake, done_id)
    _seed_claimed(fake, failed_id)

    assert repo.complete(done_id, status=CommandStatus.SUCCEEDED) is True
    assert repo.complete(done_id, status=CommandStatus.SUCCEEDED) is False
    assert repo.fail(failed_id, error_code="boom") is True
    assert repo.fail(failed_id, error_code="boom") is False

    rows = {row["id"]: row["status"] for row in fake.tables["control_commands"]}
    assert rows[str(done_id)] == "SUCCEEDED"
    assert rows[str(failed_id)] == "FAILED"


def test_renew_lease_requires_owner_and_version():
    repo, fake = _repo()
    command_id = uuid4()
    _seed_claimed(fake, command_id, version=4, claimed_by="worker-a")

    renewed = repo.renew_lease(command_id, "worker-a", version=4, lease_seconds=300)
    assert isinstance(renewed, LeaseRenewal)
    assert renewed.version == 5
    assert fake.updates[-1]["filters"]["claimed_by"] == "worker-a"
    assert fake.updates[-1]["filters"]["version"] == 4

    assert repo.renew_lease(command_id, "worker-a", version=4, lease_seconds=300) is None
    assert repo.renew_lease(command_id, "other-worker", version=5, lease_seconds=300) is None


def test_enqueue_is_idempotent_and_terminal_event_is_sequence_two():
    repo, fake = _repo()
    key = uuid4()
    first = repo.enqueue(1, "STATUS", "READ", "main", {}, key)
    second = repo.enqueue(1, "STATUS", "READ", "main", {}, key)
    assert first is not None and second is not None
    assert first.id == second.id
    assert len(fake.tables["control_commands"]) == 1
    assert [row["sequence"] for row in fake.tables["control_command_events"]] == [1]

    assert repo.append_event(first.id, 1, "info", "completed", "повтор") is False
    assert repo.append_event(first.id, 2, "info", "completed", "готово") is True
    assert [row["sequence"] for row in fake.tables["control_command_events"]] == [1, 2]
