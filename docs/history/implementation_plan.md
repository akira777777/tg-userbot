# Implementation Plan

## Overview

Convert the existing local control panel into a secure, owner-only Telegram Mini App workflow in which `@ice1land_bot` provides the entry point, a public HTTPS Next.js application provides the interface, and a local controller executes approved commands through the already-authorized Telethon userbot without exposing the user account session, Bot API token, Supabase service key, or control endpoints to the public internet.

The repository currently contains two unrelated interfaces: `web.py` plus `static/index.html` is a local, unauthenticated operational panel; `dashboard/` is a Vercel-hosted read-only Supabase dashboard protected only by a manually entered token. Neither is a Telegram Mini App. The implementation will make the Next.js application the Mini App UI, add a small Bot API controller process that responds to `/start` with an `Open control panel` Web App button, validate Telegram Web App `initData` server-side, and relay selected commands through an authenticated durable command queue. A local worker will claim commands atomically and run the existing `bot.py` CLI under its currently established per-account locking rules.

Assumption for this plan: reuse the existing Vercel project represented by `dashboard/` as the public Mini App host, assign it a stable HTTPS production URL, and run the Bot API controller plus command worker on the machine/VPS that owns `userbot.session`. The implementation must make the hosting URL an environment variable, so this assumption remains reversible.

The product boundary is an owner-operated control plane, not a public campaign tool. All operators are explicitly allowlisted by Telegram user ID. Live delivery remains opt-in per command, defaults to dry-run, requires an explicit confirmation token, and records an audit event. The existing Telegram userbot and its local SQLite database remain the execution authority; the cloud service is only an authenticated relay and read model.

### Architecture decisions and trade-offs

| Decision | Chosen approach | Why | Trade-off / mitigation |
| --- | --- | --- | --- |
| Telegram entry point | A dedicated `python-telegram-bot` polling controller sends an inline `WebAppInfo` button after `/start`. | The bot token stays only on the trusted worker host; polling avoids public webhook/tunnel requirements for the controller. | The controller must run continuously; supervise it as a long-lived service and expose a health check. |
| Mini App identity | Verify the raw `Telegram.WebApp.initData` HMAC on the Next.js server and issue a short-lived, HTTP-only session cookie only for IDs in `OWNER_TELEGRAM_IDS`. | Browser-provided identity alone is forgeable; this is Telegram's server-verifiable boundary. | Requires server-side session signing and clock-expiry validation; covered by deterministic tests. |
| Command transport | A Supabase `control_commands` queue claimed with a PostgreSQL RPC using `FOR UPDATE SKIP LOCKED`; action results and structured logs go to `control_command_events`. | Vercel cannot reach the local machine, while both sides can reach Supabase over TLS. The queue survives restarts and provides auditability. | Cloud command metadata exists; store no session data, Bot API token, full campaign text, recipient identifiers, or uploaded file bytes in the queue. |
| Command execution | A new local controller worker calls extracted, typed command services rather than shelling arbitrary browser input; transitional CLI invocation is restricted to the existing validated action allowlist. | Preserves the proven Telethon path while isolating browser-to-process input. | The current monolithic `bot.py` needs incremental extraction; retain a compatibility CLI adapter until parity tests pass. |
| Operational safeguards | Separate read-only actions, queued dry-runs, and live actions; require a second confirmation for live actions, account eligibility checks, idempotency keys, single-use confirmation nonces, rate limits, and audit events. | Prevents accidental delivery and replayed Mini App requests. | Adds one confirmation interaction; the UI must clearly show what will happen before approval. |
| Legacy panel | Keep `web.py` local-only during migration; remove its write endpoints only after feature parity and acceptance tests are complete. | Avoids breaking current local operations while the Mini App is being validated. | Two control surfaces temporarily exist; label the legacy panel as deprecated and prevent it from being externally bound. |

## Types

### Python domain models — new `control/models.py`

Define strict, serializable Pydantic v2 models. Reject unknown fields (`extra="forbid"`) and enforce bounded strings, positive identifiers, and finite numeric values at the API boundary.

- `CommandKind(str, Enum)`: `STATUS`, `GROUPS`, `CAMPAIGNS`, `MEDIA_LIST`, `BLACKLIST_ADD`, `BROADCAST`, `DMSEND`, `CONDUCT`, `STOP`.
  - Initial Mini App scope exposes `STATUS`, `GROUPS`, `CAMPAIGNS`, `MEDIA_LIST`, `BLACKLIST_ADD`, and queued dry-run previews.
  - `BROADCAST`, `DMSEND`, and `CONDUCT` are live-capable only behind `CommandMode.LIVE` and a valid confirmation nonce.
  - File upload, account import/removal, privacy changes, fan-out, and raw CLI options are deliberately excluded from phase 1 because they are privileged/destructive and have no safe remote approval model yet.
- `CommandMode(str, Enum)`: `READ`, `DRY_RUN`, `LIVE`.
- `CommandStatus(str, Enum)`: `PENDING`, `CLAIMED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `EXPIRED`.
- `TelegramIdentity(BaseModel)`: `telegram_user_id: int`, `username: str | None`, `first_name: str | None`, `auth_date: datetime`, `query_id: str | None`. Do not persist raw `init_data`.
- `CommandRequest(BaseModel)`: `kind`, `mode`, `account: AccountName = "main"`, `payload: CommandPayload`, `idempotency_key: UUID`, `confirmation_nonce: UUID | None`.
- Discriminated payload union (`CommandPayload`) with one model per operation:
  - `ReadPayload()`;
  - `BlacklistAddPayload(value: str, reason: str)`;
  - `BroadcastPayload(text: str | None, media_id: int | None, group_ids: list[int], delay_min: Decimal, delay_max: Decimal, max_runs: int)`;
  - `DmSendPayload(text: str | None, media_id: int | None, group_id: int | None, limit: int, delay_min: Decimal, delay_max: Decimal, repeat_ok: bool)`;
  - `ConductPayload(kind: Literal["broadcast", "dm"], text: str | None, media_id: int | None, group_ids: list[int], group_id: int | None, limit: int, window_hours: Decimal, repeat_ok: bool)`;
  - `StopPayload(command_id: UUID | None, account: AccountName | Literal["all"])`.
- `CommandRecord(BaseModel)`: immutable queue representation with `id`, `request`, `requested_by`, `status`, `created_at`, `claimed_at`, `finished_at`, `lease_expires_at`, `result_summary`, `error_code`, and `version`.
- `CommandEvent(BaseModel)`: `command_id`, `sequence`, `occurred_at`, `level`, `event_type`, `safe_message`. Event text must be redacted and bounded; never return raw process arguments, API credentials, session paths, or recipient IDs.
- `ConfirmationIntent(BaseModel)`: canonical, server-generated summary for a live operation, owner ID, expiry (five minutes), nonce, and SHA-256 digest of canonical request content.

### TypeScript types — new `dashboard/lib/control-types.ts`

Mirror the JSON response contracts using discriminated unions rather than untyped `any` values:

- `MiniAppBootstrap`, `CommandKind`, `CommandMode`, `CommandStatus`, `ControlCommand`, `ControlCommandEvent`, `ConfirmationIntent`, and operation-specific payload interfaces.
- `ApiError` with stable `code`, user-safe `message`, and optional `retryAfterSeconds`.
- Do not duplicate validation logic in TypeScript as the source of truth; use it for rendering and submit server-side schema-validated JSON.

### Database schema — extend `supabase/schema.sql`

Add migrations, never hand-edit a deployed schema without a migration record:

- `control_commands`:
  - `id uuid primary key`, `idempotency_key uuid not null`, `owner_telegram_id bigint not null`, `kind text not null`, `mode text not null`, `account text`, `payload jsonb not null`, `request_digest text not null`, `status text not null`, `created_at timestamptz not null default now()`, `claimed_by text`, `claimed_at timestamptz`, `lease_expires_at timestamptz`, `finished_at timestamptz`, `result_summary jsonb`, `error_code text`, `version integer not null default 0`.
  - Unique index on `(owner_telegram_id, idempotency_key)`.
  - Partial index for queue polling: `(status, created_at)` where status in `('pending', 'claimed', 'running')`.
  - Check constraints for valid kind/mode/status values and bounded JSON payload size.
- `control_command_events`:
  - `command_id uuid references control_commands(id) on delete cascade`, `sequence integer`, `occurred_at timestamptz`, `level text`, `event_type text`, `safe_message text`, primary key `(command_id, sequence)`.
- `control_confirmations`:
  - `nonce uuid primary key`, `owner_telegram_id bigint`, `request_digest text`, `expires_at timestamptz`, `consumed_at timestamptz`, `created_at timestamptz`.
- `claim_control_commands(worker_id text, batch_size integer, lease_seconds integer)` PostgreSQL function. It must atomically select pending or expired leases using `FOR UPDATE SKIP LOCKED`, set lease fields, increment version, and return the claimed records.
- `append_control_command_event(...)` RPC that allocates the next event sequence safely.
- Enable RLS. No `anon` or `authenticated` role can select or mutate control tables directly. Only `service_role` accesses them through server-side Next.js routes and the trusted worker. Remove `anon_read` from sensitive existing aggregate tables or move public-safe aggregate data to a dedicated minimal view; blacklist contents must never be anonymous-readable.

## Files

### New files

- `control/__init__.py` — local controller package marker.
- `control/config.py` — typed environment configuration (`BOT_TOKEN`, `MINI_APP_URL`, `OWNER_TELEGRAM_IDS`, Supabase URL/service credential, worker identifier, polling and lease values). Fail closed when a required setting is absent or malformed.
- `control/models.py` — Pydantic command, event, identity, and confirmation types listed above.
- `control/telegram_auth.py` — Telegram Mini App `initData` parser and HMAC verification; validates freshness and owner allowlist.
- `control/queue.py` — Supabase queue repository with parameterized RPC calls, idempotent enqueue, claim/renew/complete/fail/cancel methods, and event append helpers.
- `control/policy.py` — central operation policy: allowlist, payload limits, account checks, read/dry/live classification, confirmation requirements, rate limits, and redaction rules.
- `control/executor.py` — maps a `CommandRecord` to a typed internal userbot operation; guarantees one active command per account/conductor, supports cancellation, and writes terminal status exactly once.
- `control/worker.py` — long-running local command worker with lease renewal, back-off, shutdown handling, stale-command recovery, structured local logging, and health endpoint/heartbeat.
- `control/controller_bot.py` — `python-telegram-bot` polling process. `/start` verifies the sender against the owner allowlist and returns a `WebAppInfo(MINI_APP_URL)` button; unauthorized users receive no operational detail. `/status` returns a non-sensitive worker health summary.
- `control/main.py` — explicit CLI entry points: `telegram-bot`, `worker`, and `healthcheck`; no implicit network calls on import.
- `dashboard/lib/env.js` — required server-only environment validation; values are never imported into client components.
- `dashboard/lib/telegram.js` — client helper that obtains `window.Telegram.WebApp.initData` only after mounting, expands the Mini App, applies Telegram theme variables, and handles unavailable desktop-browser mode safely.
- `dashboard/lib/api.js` — typed client calls to internal `/api/control/*` routes with a single retry-safe JSON error parser.
- `dashboard/app/api/control/session/route.js` — server endpoint that validates `initData`, checks owner access, and issues a short-lived `HttpOnly`, `Secure`, `SameSite=Lax` signed cookie.
- `dashboard/app/api/control/bootstrap/route.js` — authenticated owner-only bootstrap endpoint returning sanitized state, permitted actions, and active/recent command summaries.
- `dashboard/app/api/control/commands/route.js` — authenticated `POST` endpoint for validated command enqueue and `GET` endpoint for owner-scoped command history.
- `dashboard/app/api/control/commands/[id]/route.js` — authenticated owner-scoped command detail/event polling and cancellation endpoint.
- `dashboard/app/api/control/confirmations/route.js` — creates a short-lived confirmation intent from canonical server-side request data; never trusts a browser-provided summary.
- `dashboard/components/mini-app-shell.js` — client boundary handling authentication bootstrap, connection/error/loading state, and Telegram viewport adaptation.
- `dashboard/components/command-form.js` — accessible forms with bounded inputs, dry-run default, preflight review, disabled double-submit state, and live confirmation flow.
- `dashboard/components/command-timeline.js` — keyboard-accessible status/event list with terminal/error states; event content inserted as text, not raw HTML.
- `dashboard/components/operations-dashboard.js` — read-only cards and tables replacing the current direct Supabase data rendering with owner-authorized API data.
- `dashboard/lib/control-types.ts` — TypeScript response/request contracts.
- `tests/conftest.py` — isolated temporary SQLite database and environment fixtures; no test can load real sessions, production `.env`, or `userbot.db`.
- `tests/test_command_models.py` — schema limits, invalid inputs, and serialization tests.
- `tests/test_telegram_auth.py` — HMAC verification using Telegram-compatible fixtures, tampered fields, duplicate keys, stale `auth_date`, unauthorized owner, and missing data cases.
- `tests/test_policy.py` — permission matrix, confirmation requirements, dry-run defaults, redaction, idempotency, payload bounds, and forbidden action tests.
- `tests/test_queue.py` — integration tests against isolated Supabase/PostgreSQL test schema or a repository fake with explicit contention/lease scenarios.
- `tests/test_executor.py` — executor mapping, account-slot locking, terminal transitions, cancellation, lease expiry, and failure redaction.
- `tests/test_controller_bot.py` — `/start`, allowlist rejection, Web App button URL, and health response tests using the official application test harness.
- `dashboard/__tests__/` — Node tests for Telegram client bootstrap, API response rendering, error states, confirmation UI, and no-JavaScript/unsupported-environment fallback.
- `docs/architecture/adr-001-telegram-mini-app-control-plane.md` — decision record for local-worker-plus-queue architecture.
- `docs/operations/mini-app-runbook.md` — environment setup, BotFather configuration, start/restart/rollback procedure, owner onboarding, token rotation, and incident response.
- `docs/security/threat-model.md` — assets, trust boundaries, abuse cases, mitigations, and verification evidence.

### Existing files to modify

- `bot.py`
  - Extract pure database selection, campaign planning, and guarded execution boundaries into importable modules without changing the existing CLI contract in the first migration.
  - Replace raw `argparse.Namespace` coupling in internal functions with typed request objects or narrow adapters.
  - Preserve Telethon session ownership and all existing FloodWait, daily-budget, blacklist, cross-account deduplication, and per-account restrictions.
  - Add transaction boundaries around campaign creation/status writes and record execution origin as `cli`, `legacy-web`, or `mini-app` without recording bot credentials or raw identity metadata.
- `web.py`
  - Keep binding hard-coded to loopback during migration.
  - Add a deprecation banner and restrict it to local operator use; do not expose it through a tunnel or reverse proxy.
  - Refactor shared command construction into the new typed policy/executor path where safe, then delete duplicated validation once parity tests pass.
  - Add explicit request-size/file-type limits for its transitional upload endpoint, and ensure temporary upload files are removed after terminal processing.
- `static/index.html`
  - Retain only as the legacy local console during phase 1; display migration status and no longer present it as a Mini App.
  - Replace DOM `innerHTML` interpolation with DOM node construction wherever any field can originate from SQLite/process output during the hardening pass.
- `dashboard/app/page.js`
  - Replace manual `DASH_TOKEN` entry in `localStorage` with Telegram Mini App session bootstrap.
  - Present owner identity, connection state, safe command queue, preflight review, confirmation, live status timeline, empty/error/loading states, and a clear link/notice for unavailable commands.
  - Preserve useful aggregate cards, but do not fetch Supabase directly from the browser or render blacklisted people, recipient IDs, raw command text, session names, or secrets.
- `dashboard/app/api/data/route.js`
  - Deprecate and remove its manually supplied bearer token model after Mini App control routes reach parity.
  - Do not return `tb_blacklist` or other sensitive rows. If retained temporarily, bind it to the same signed owner session and return an explicit versioned, minimal contract.
- `dashboard/app/layout.js`
  - Add correct Telegram Mini App metadata, viewport configuration, Russian locale labels, and accessible document title/description.
- `dashboard/app/globals.css`
  - Implement Telegram theme-variable fallbacks, responsive mobile-first layout, focus states, minimum touch targets, reduced-motion handling, and status colors that meet contrast requirements.
- `dashboard/package.json` and `dashboard/package-lock.json`
  - Pin a supported Next.js release that resolves the current high-severity PostCSS advisory. Do not use `npm audit fix --force` blindly; perform an intentional compatibility update, update the lockfile, and prove build/test compatibility.
  - Add only maintained testing dependencies required by the selected test runner; do not add a second UI framework.
- `supabase/schema.sql`
  - Split bootstrap schema from versioned SQL migrations, add the queue/RPC/RLS changes above, and make repeat execution idempotent.
  - Remove the unsafe `anon_read` policy for sensitive operational data.
- `.env.example`
  - Document placeholders only: `BOT_TOKEN`, `MINI_APP_URL`, `OWNER_TELEGRAM_IDS`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CONTROL_SESSION_SECRET`, `WORKER_ID`, and bounded queue settings.
  - Never add actual tokens, session paths, production URLs tied to secrets, or user IDs.
- `.gitignore`
  - Add `.serena/`, `.claude-flow/`, `dashboard/coverage/`, test artifacts, and controller runtime state; keep all `.env*`, session, SQLite, media-cache, and Vercel credential artifacts excluded.
- `pyproject.toml`
  - Align the package/test configuration with the actual repository layout before expanding features: either create the declared `src/tg_userbot` package as part of an explicit migration or configure the existing flat modules honestly.
  - Move duplicate development dependencies into one canonical list, add a tested lockfile strategy, and make coverage/test paths match real files.
- `requirements.txt`
  - Make it either a generated runtime export or remove it in favor of the canonical project metadata; it currently disagrees with `pyproject.toml` and lacks the declared controller dependency.
- `pyrightconfig.json`
  - Replace malformed and duplicate options with a valid strict, scoped configuration; do not suppress unknown types globally.
- `Makefile`, `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `.github/workflows/ci.yml`
  - Correct every reference to the non-existent `src/tg_userbot`, tests directory, Poetry lockfile, and unsupported Docker assumptions.
  - Split trusted local worker/controller services from the public frontend; do not place `userbot.session` or Bot API token in the Vercel deployment or container image.
  - Add CI jobs that run Python tests/type/lint/security checks and dashboard install/audit/build/tests with a lockfile-enforced install.
- `README.md`, `РУКОВОДСТВО.md`, `docs/architecture/ARCHITECTURE.md`
  - Replace aspirational/inaccurate architecture statements with the actual trust boundaries, commands, deployment topology, data retention rules, and operator workflow.

### Files to delete or move

- Move root `test_suite.py`, `test_orchestra.py`, `test_media.py`, and `test_api_live.py` into `tests/` with pytest test functions and fixtures. Do not delete their coverage until equivalent automated tests exist.
- Delete generated/untracked `.serena/` and `.claude-flow/` only after confirming they are tool artifacts rather than user-authored project settings; this is repository hygiene, not an implementation dependency.
- Delete the old bearer-token dashboard endpoint and client token storage only after the Telegram `initData` session flow is deployed and accepted.
- Do not delete `web.py` or `static/index.html` in the first release; remove them only after an explicit successful rollback window and feature-parity acceptance test.

## Functions

### New Python functions

- `verify_web_app_init_data(init_data: str, bot_token: SecretStr, now: datetime, max_age: timedelta) -> TelegramIdentity` in `control/telegram_auth.py` — parse URL-encoded data without lossy normalization, exclude `hash`, derive `WebAppData` HMAC key, constant-time compare the hash, validate age, and return only safe identity fields.
- `is_owner(identity: TelegramIdentity, owner_ids: frozenset[int]) -> bool` in `control/policy.py` — returns `False` by default; no username-based authorization.
- `validate_command_request(identity: TelegramIdentity, request: CommandRequest, now: datetime) -> ValidatedCommand` in `control/policy.py` — enforces action/mode/payload/owner/rate/confirmation policy before enqueueing.
- `create_confirmation(identity: TelegramIdentity, request: CommandRequest, now: datetime) -> ConfirmationIntent` in `control/policy.py` — canonicalizes only the server-validated request and creates a five-minute, single-use nonce.
- `enqueue_command(validated: ValidatedCommand) -> CommandRecord` in `control/queue.py` — idempotently inserts a pending command and its audit event.
- `claim_commands(worker_id: str, limit: int, lease: timedelta) -> list[CommandRecord]` in `control/queue.py` — calls the queue RPC and never reads commands belonging to a different deployment/environment.
- `renew_lease(command_id: UUID, worker_id: str, version: int) -> CommandRecord | None` in `control/queue.py` — prevents a slow worker from completing a command claimed by a successor.
- `execute_command(command: CommandRecord, runtime: UserbotRuntime) -> ExecutionResult` in `control/executor.py` — performs only allowlisted typed operations and provides cancellation checkpoints.
- `run_worker(settings: ControlSettings) -> None` in `control/worker.py` — polling/backoff/lease-renewal supervisor; handles `SIGINT`/Windows termination gracefully and leaves a reclaimable lease on crash.
- `build_application(settings: ControlSettings) -> Application` in `control/controller_bot.py` — creates the polling bot with `/start`, `/status`, and fallback handlers, no global singleton.
- `make_web_app_keyboard(mini_app_url: str) -> InlineKeyboardMarkup` in `control/controller_bot.py` — creates the Bot API Web App button used after `/start`.

### Modified Python functions

- `bot.connect()` in `bot.py` — become a narrow SQLite repository factory with explicit path injection for tests and no hidden reliance on process environment after startup.
- `bot.cmd_broadcast`, `bot.cmd_dmsend`, and `bot.cmd_conduct` — preserve behavior but delegate planning/execution to testable functions, accept an explicit execution origin, and produce sanitized structured event records in addition to terminal output.
- `bot.send_run()` and conductor workers — ensure idempotent sent-record semantics under retries, check cancellation between targets, and commit final campaign state on success/failure/cancel.
- `web.build_args()` — becomes a legacy adapter around `CommandRequest` validation; remove direct browser-to-CLI argument construction after the executor is in place.
- `web.api_media_upload()` — enforce configured byte limit, allowlisted MIME/content sniffing, collision-safe temporary storage, cleanup after failed and successful jobs, and no unsafe browser-controlled filesystem path.
- `web.api_stream()` — retain local log streaming only; add bounded connection lifetime and no raw sensitive process output once structured events replace text logs.

### New Next.js route functions

- `POST /api/control/session` — validate Telegram `initData`, enforce owner access, issue/refresh the signed session cookie, and rate-limit failures without revealing whether a user ID is configured.
- `GET /api/control/bootstrap` — return only current owner-safe state and capabilities.
- `POST /api/control/confirmations` — validate a candidate live command, create confirmation state, and return the server-created summary/digest/expiry.
- `POST /api/control/commands` — validate session, origin, CSRF/nonce, idempotency key, and request; enqueue or return the existing command for an idempotent repeat.
- `GET /api/control/commands` and `GET /api/control/commands/:id` — owner-scoped status/event polling, pagination, and explicit cache-control headers.
- `POST /api/control/commands/:id/cancel` — transitions only the owner’s pending/claimed command to cancellation; an already-running executor receives a cancellation request rather than a process-kill from the browser.

### Removed functions / migration strategy

- Remove `dashboard/app/api/data/route.js::GET` only after the Mini App bootstrap endpoint reproduces the required read-only cards and no client stores `dash_token`.
- Remove browser writes to `localStorage["dash_token"]` at the same time; no migration of this secret is required because it must not be reused.
- Do not remove the existing `web.py` command endpoints until Mini App acceptance tests cover each retained operation and the local-only fallback has had a documented rollback period.

## Classes

### New Python classes

- `ControlSettings` (`control/config.py`) — `BaseSettings` configuration with validation and redacted display; owns environment parsing and must not log secret values.
- `ControlQueueRepository` (`control/queue.py`) — interface/protocol plus Supabase implementation; methods are explicit transaction-like operations, enabling a fake implementation for unit tests.
- `CommandPolicy` (`control/policy.py`) — immutable policy configuration and validation methods; no database or network I/O.
- `CommandExecutor` (`control/executor.py`) — owns process/runtime execution, per-account locks, cancellation registry, and terminal-state handoff.
- `ControlWorker` (`control/worker.py`) — owns work loop, concurrency limit, leases, health heartbeat, graceful shutdown, and retry/backoff policy.
- `Redactor` (`control/policy.py`) — removes token-like strings, filesystem session paths, long numeric recipient identifiers, and unsafe raw exception details before an event enters the queue/UI.

### New React components

- `MiniAppShell` (`dashboard/components/mini-app-shell.js`) — establishes Telegram session and renders login/unsupported/loading/error/app states.
- `OperationsDashboard` (`dashboard/components/operations-dashboard.js`) — owner-scoped aggregate state and command history.
- `CommandForm` (`dashboard/components/command-form.js`) — one declarative form per supported operation; emits typed request bodies and never enables live mode by default.
- `CommandReviewDialog` (`dashboard/components/command-review-dialog.js`) — shows canonical server response, requires explicit acknowledgement, and consumes a confirmation nonce once.
- `CommandTimeline` (`dashboard/components/command-timeline.js`) — presents queued/running/terminal status and sanitized events without HTML injection.

### Modified classes

- `web.Bus` in `web.py` — remains a local compatibility event bus; stop treating it as cross-device state once `ControlQueueRepository` owns durable command/event history.

### Removed classes

- No class is removed in the first implementation release. The legacy `Bus` becomes removable only after no UI or test depends on SSE from `web.py`.

## Dependencies

- Keep the current `aiohttp` and Telethon runtime for the local legacy panel/userbot until the migration completes.
- Use the already declared, maintained `python-telegram-bot` package for the controller process; verify installed version and current API against official documentation before implementation. Do not add a second Bot API library.
- Keep the existing Supabase Python client only if it supports the required RPC and service-role workflow with the resolved version; otherwise use a narrowly scoped async HTTP client already present in the project with explicit timeouts. Choose one client path, not both.
- Add `cryptography` only if the selected session-cookie implementation needs it; prefer a maintained, framework-appropriate signed-cookie solution already available in the Next.js deployment rather than rolling custom crypto.
- Add an intentionally chosen maintained dashboard test stack (for example, Vitest plus React Testing Library) only after checking its current Next.js compatibility; add `eslint`/Next lint only if it is configured as an executable project check rather than dead configuration.
- Update Next.js intentionally to a supported release that fixes the observed high-severity PostCSS advisory. The current `npm audit` reports two vulnerabilities and only offers a breaking major upgrade; evaluate release notes, migrate, lock, build, and test rather than applying `--force`.
- Add `pytest-cov`, `ruff`, `mypy`, `pyright`, and `bandit` to the actual development environment from the canonical lockfile so the declared checks are executable. Current local environment contains pytest but not pytest-cov, causing test collection to fail before tests run.
- Choose one packaging path. Preferred: migrate the existing flat modules into the declared `src/tg_userbot/` package with thin CLI entry points, add `uv.lock` or `poetry.lock` consistently, and update all commands. Do not retain contradictory setuptools/Poetry/requirements metadata.

## Testing

### Baseline findings already verified

- `python -m py_compile bot.py web.py cloud.py` — passed.
- `.venv\Scripts\python.exe test_suite.py` — passed in its temporary SQLite environment.
- `.venv\Scripts\python.exe test_orchestra.py` — passed in its temporary SQLite/session environment.
- `npm run build` in `dashboard/` — passed with Next.js `15.5.29`.
- `.venv\Scripts\python.exe -m pytest --collect-only -q` — failed before collection because `pyproject.toml` injects `--cov` options but `pytest-cov` is not installed; this is a configuration defect to fix before treating pytest as green.
- `npm audit --omit=dev --audit-level=high` — reports one high and one moderate dependency vulnerability through the current Next.js/PostCSS dependency tree; remediation must be verified as part of the dependency update.

### Required automated tests

1. Convert root assertion scripts to pytest test functions with isolated filesystem/database fixtures. A normal test command must never touch `userbot.db`, `.env`, real session files, Supabase production tables, Saved Messages, or the network.
2. Unit-test every Pydantic command model, including empty text/media combinations, Unicode boundaries, malformed UUIDs, maximum group counts, negative/NaN delays, invalid account names, and unsupported command types.
3. Test Telegram `initData` validation with known-good signed fixture data and negative cases: changed key/value, reordered/canonical encoding edge cases, duplicate key, absent hash, stale authentication, future timestamp policy, bad token, unauthorized ID, and constant-time comparison path.
4. Test authorization at every Next.js endpoint: missing cookie, forged cookie, expired cookie, wrong owner, cross-owner command lookup/cancel, malformed JSON, missing idempotency key, and CSRF/Origin rejection.
5. Test the command policy matrix: read actions need no confirmation; dry-runs cannot deliver; live actions require an unexpired matching nonce; a nonce cannot be reused; unavailable privileged commands are rejected server-side even if constructed manually.
6. Test idempotency and concurrency with at least two simultaneous enqueue attempts using the same key, two worker claims, expired lease reclaim, lease renewal failure, terminal-state races, cancellation before claim, cancellation during execution, worker crash, and restart recovery.
7. Test executor mapping with a fake userbot runtime: validate the exact typed operation invoked, account lock behavior, FloodWait propagation, budget/blacklist/dedup refusal, event ordering, redaction, and no raw exception leakage.
8. Test the Bot API controller with its supported test harness: owner `/start` receives a Web App button with the configured HTTPS URL; non-owner receives a generic denial; no token/owner ID leaks into replies; `/status` is non-sensitive.
9. Add Next.js component/route tests for Telegram bootstrap success/failure, desktop fallback, load/error/empty states, theme application, minimum keyboard path, double-submit prevention, review dialog, confirmation expiry, command timeline polling, and text escaping.
10. Add browser-level smoke tests against local mock API responses: Mini App shell loads, mock `initData` starts a session, a dry-run queues and reaches a terminal view, an unconfirmed live action does not queue, and an unauthorized session sees no data.
11. Add database migration tests on a clean Supabase/PostgreSQL instance: schema idempotence, RLS denial for anon/authenticated roles, service-role RPC paths, queue indexes, and atomic `SKIP LOCKED` claim behavior.
12. Add a manual staging acceptance checklist: set a non-production bot token and one staging owner; use BotFather’s **Configure Mini App** with the staging HTTPS URL; open from the Telegram Android/iOS client; verify identity, theme, safe read-only status, dry-run, expired confirmation, cancellation, worker offline UI, worker restart recovery, and a deliberately blocked non-owner account. No live campaign is part of automated or staging acceptance unless the operator explicitly confirms a consented test target.

### Mandatory validation commands after implementation

Run these only after package layout has been corrected; the final commands will be recorded exactly in the runbook and CI:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy .
.\.venv\Scripts\python.exe -m pyright
.\.venv\Scripts\python.exe -m bandit -r .
Push-Location dashboard; npm ci; npm run test; npm run build; npm audit --omit=dev --audit-level=high; Pop-Location
docker compose config
```

Add controlled integration commands using a dedicated staging Supabase project and synthetic local database only. Do not point tests at production credentials or a real Telegram account.

## Implementation Order

1. Freeze the trust boundary: verify the BotFather token rotation is complete, remove any old token from local files/logs/shell history where applicable, add secret scanning to CI, and confirm that no credentials are tracked by Git. Do not commit `.env`, session files, Vercel environment files, SQLite databases, or generated tool state.
2. Correct the project baseline before feature work: select one package/dependency strategy, repair `pyproject.toml`, test discovery, coverage configuration, `pyrightconfig.json`, requirements/lockfile alignment, Makefile, Docker, Compose, and CI references. Convert the existing root tests to isolated pytest tests and make baseline quality commands executable.
3. Document the actual architecture and write ADR-001 plus the threat model. Explicitly classify the project as a single-owner MVP with a local trusted worker, one public Mini App frontend, and a cloud relay — not the currently documented multi-service/Redis/event-sourcing system.
4. Create versioned Supabase migrations for the command queue, events, confirmations, RLS, RPC functions, indexes, and safe aggregate views. Apply them only to a staging project first; prove RLS behavior and atomic claiming.
5. Add the Python control package: typed settings, models, HMAC `initData` verification, owner allowlist, policy engine, redaction, repository interface, and unit tests. No UI or command execution is added until all validation tests pass.
6. Implement the local queue worker with dry-run/read operations first. Use a fake userbot runtime in tests; then integrate the existing userbot through typed adapters while preserving per-account ownership, FloodWait, budgets, blacklists, and cross-account deduplication.
7. Implement the `python-telegram-bot` controller, its `/start` Web App button, owner authorization, health/status command, graceful shutdown, and service supervision. Prove it in staging with an owner and a non-owner before configuring production BotFather settings.
8. Upgrade/harden the Next.js dashboard dependencies intentionally, then add validated server-only environment configuration, Telegram session route, secure cookie handling, owner-scoped queue routes, confirmation route, and route tests.
9. Replace the current token-in-localStorage page with the Mini App shell, operations dashboard, command forms, review/confirmation flow, event timeline, Telegram theme support, mobile accessibility, error/empty/offline states, and component/browser tests.
10. Migrate read-only dashboard data to the authenticated bootstrap contract. Remove sensitive `anon` access and stop returning blacklist data. Preserve only the aggregate visibility required by the Mini App.
11. Harden the legacy local panel: retain loopback binding, apply upload/temporary-file cleanup and DOM-output safety fixes, mark it deprecated, and route shared validations through the new policy layer without changing current userbot behavior prematurely.
12. Run the complete automated suite, static analysis, dependency audit, Docker Compose validation, staging database/RLS tests, and manual Telegram-client acceptance checklist. Fix every reproducible failure before deployment.
13. Deploy the frontend to a stable HTTPS staging URL, configure it in BotFather’s Mini App setting, deploy/restart the trusted controller and worker, and verify an end-to-end dry-run from `/start` in the Telegram client to a completed local command event.
14. Promote to production only after staging evidence is retained. Start with read-only and dry-run operations enabled; enable live actions individually after explicit operator acceptance, and maintain the local legacy panel as a rollback path for one release window.
15. After the rollback window and parity checklist succeed, remove the old manual dashboard token flow and obsolete duplicate endpoint code, update the runbook, and verify that the public surface cannot issue any command without a valid Telegram-owner session and confirmation.
# UPDATE — Приватность forward и функционал Mini App (дополнение к основному плану)

## Обновления к Types

### ForwardPrivacyMode (bridge/privacy_forward.py)
```python
class ForwardPrivacyMode(str, Enum):
    DISABLED = "disabled"          # не проверять, поведение по умолчанию Telethon
    STRIP_ATTRIBUTION = "strip"    # отправлять копию без forward-атрибуции
    SEND_COPY = "send_copy"        # send_message/send_file вместо forward_messages
```

### AccountAlias (frontend/src/shared/types/account.ts)
Реальные имена аккаунтов (`main`, `work2`, ...) никогда не отображаются в UI вместе с user_id или номером телефона. В интерфейсе используется псевдоним:
```ts
export interface AccountAlias {
  internal_name: string;   // "main", "work2" — только для бэкенда
  display_name: string;    // "Аккаунт 1", "Аккаунт 2" — для UI
  is_eligible: boolean;
}
```

### MiniAppButton (frontend/src/features/buttons/button-defs.ts)
Явное описание всех кнопок Mini App:
| Кнопка | Действие | Команда userbot | Требует подтверждения |
|--------|----------|-----------------|----------------------|
| Сбор информации | scrape | `groups --sync` / `scrape` | Нет (только чтение) |
| Сбор юзеров | collect_users | `scrape --groups <id>` | Нет (только чтение) |
| Рассылка по чатам | broadcast_chat | `conduct --what broadcast` | Да (LIVE) |
| Рассылка по каналам | broadcast_channel | `conduct --what broadcast` | Да (LIVE) |
| Рассылка по юзерам | dm_users | `conduct --what dm` | Да (LIVE) |
| Добавить медиа | media_add | `media add <path>` | Нет (только чтение/запись файла) |
| Настройка интервала | set_interval | конфиг CampaignConfig | Нет |
| Запуск | start_campaign | `conductor` | Да |
| Остановка | stop_campaign | `stop` | Нет |

## Обновления к Files

### Новые файлы
- `bridge/privacy_forward.py` — ForwardPrivacyManager: отключает forward-атрибуцию при пересылке (использует `send_message`/`send_file` вместо `forward_messages`, удаляет `forward_from`, `forward_from_chat`, `forward_date` из логов и ответов API).
- `bridge/account_alias.py` — AccountAliasMapper: маппинг внутренних имён аккаунтов на отображаемые псевдонимы в UI; гарантирует, что в ответах API и логах не попадают реальные user_id аккаунтов.
- `frontend/src/features/buttons/button-defs.ts` — определения кнопок Mini App.
- `frontend/src/features/buttons/ButtonBar.tsx` — панель кнопок управления рассылками.

### Изменения в существующих файлах
- `bot.py`: параметр `forward_privacy: bool` в `get_client()` / `send_run()`; при `True` используется `send_message`/`send_file` вместо `forward_messages`, в `sent` не пишется `forward_from`.
- `bridge/executor.py`: перед выполнением любой команды рассылки применяется `ForwardPrivacyManager.strip()`.
- `bridge/policy.py`: `Redactor` дополнительно маскирует account-имена в логах до `AccountAlias.display_name`.
- `frontend/src/shared/types/account.ts`: добавлен `AccountAlias`.

## Обновления к Functions

| Имя | Сигнатура | Файл | Назначение |
|-----|-----------|------|------------|
| `strip_forward_metadata` | `(msg: Message) -> dict` | `bridge/privacy_forward.py` | Удаляет `forward_from`, `forward_from_chat`, `forward_date`, `forward_sender_name` из данных сообщения перед записью в лог или API. |
| `send_without_forward` | `(client, peer, message, media?) -> Message` | `bridge/privacy_forward.py` | Отправляет сообщение через `send_message`/`send_file`, никогда не через `forward_messages`. |
| `map_account_to_alias` | `(internal_name: str) -> AccountAlias` | `bridge/account_alias.py` | Возвращает псевдоним для UI; внутреннее имя не возвращается клиенту. |
| `redact_account_identity` | `(text: str) -> str` | `bridge/policy.py` | Заменяет вхождения имён аккаунтов и user_id на псевдонимы перед записью в `control_command_events`. |

## Обновления к Classes

| Имя | Файл | Ключевые методы | Назначение |
|-----|------|-----------------|------------|
| `ForwardPrivacyManager` | `bridge/privacy_forward.py` | `strip()`, `send_safe()`, `is_enabled()` | Гарантирует, что пересылка не раскрывает исходного отправителя. |
| `AccountAliasMapper` | `bridge/account_alias.py` | `to_alias()`, `to_internal()` | Разделяет внутренние имена аккаунтов от отображаемых в Mini App. |

## Обновления к Testing

- `tests/test_privacy_forward.py` — проверка, что `send_without_forward` не вызывает `forward_messages`, что в ответных данных нет `forward_from`/`forward_from_chat`.
- `tests/test_account_alias.py` — проверка, что API не возвращает внутренние имена аккаунтов и user_id, только псевдонимы.
- `frontend/src/__tests__/privacy.test.ts` — проверка, что в UI отображаются только `AccountAlias.display_name`, нет реальных имён сессий.

## Обновления к Implementation Order

Добавить после шага 5 (Python control package):
5.5. Реализовать `ForwardPrivacyManager` и `AccountAliasMapper`; unit-тесты на отсутствие forward-атрибуции и маскировку account-идентов.