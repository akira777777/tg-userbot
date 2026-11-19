# API reference

The project does not expose a public FastAPI or JWT API. Remote control goes through the
Next.js Mini App, and the legacy panel stays on localhost. See
[`../architecture/ARCHITECTURE.md`](../architecture/ARCHITECTURE.md) for the trust model and
[`../telegram/README.md`](../telegram/README.md) for Mini App identity.

## Mini App server routes

All `/api/control/*` routes require the signed owner session created from Telegram `initData`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/control/session` | Verify Telegram `initData` and set the session cookie |
| GET | `/api/control/bootstrap` | Read the reduced Supabase snapshot |
| POST | `/api/control/confirmations` | Create a five-minute confirmation for a LIVE delivery |
| POST | `/api/control/commands` | Enqueue a supported command |
| GET | `/api/control/commands` | List the owner's commands |
| GET | `/api/control/commands/{id}` | Read one command |

Supported command kinds are `STATUS`, `GROUPS`, `CAMPAIGNS`, `MEDIA_LIST`,
`BLACKLIST_ADD`, `BROADCAST`, `DMSEND`, `CONDUCT`, and `STOP`. Read-only kinds require
mode `READ`. Delivery kinds require `DRY_RUN` or `LIVE`; `LIVE` also requires the
confirmation nonce. Unsupported kinds are rejected before enqueue.

`DRY_RUN` always passes `--dry-run` to `bot.py`. `LIVE` omits it and can send Telegram
messages.

### Account aliases

Internal account names (`main`, `work2`, …) never leave the server. `GET /api/control/bootstrap`
returns `account_aliases: [{ key, display_name }]` (keys `acc-1`, `acc-2`, … in the server's
canonical order) and `accounts` rows carry `key`/`display_name` instead of `name`. The UI
selects accounts by key. `POST /api/control/commands` and `POST /api/control/confirmations`
translate keys back to internal names before enqueue; internal names are still accepted for
backward compatibility. Command lists return `account_display` instead of `account`.

### Local mode

For a plain local web page without Telegram sign-in set `CONTROL_LOCAL_MODE=1` in the
dashboard environment (`dashboard/.env.local`). In local mode `requireOwnerSession` returns
the fixed owner from `LOCAL_OWNER_ID` (default `1`), `POST /api/control/session` succeeds
without `initData`, and bootstrap reads the local `userbot.db` directly (via `node:sqlite`,
read-only) when Supabase is not configured: accounts, groups, campaigns, and computed stats
appear as they would from the cloud mirror. Override the database path with `LOCAL_DB_PATH`.

Write routes answer `503` with «панель работает в режиме просмотра» when the cloud queue
is absent. Requires Node >= 22.5 for the built-in `node:sqlite`.

The flag is ignored when `NODE_ENV=production`: a public deployment can never open the
control plane without a signed Telegram owner session.

### Worker heartbeat

`GET /api/control/bootstrap` includes `worker: { last_heartbeat_at, online, stale_seconds }`.
The local worker updates `tb_worker_heartbeat` every poll tick; `online` is `false` when the
last signal is older than `WORKER_STALE_SECONDS` (default 600 = two lease intervals), and
`null` when the table or data is missing.

## Local panel

`web.py` listens only on `127.0.0.1:8642`. Writes on that panel do not ask for a separate
token; it is protected by loopback binding only.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | `static/index.html` |
| GET | `/api/state` | Read-only snapshot of accounts, groups, media, campaigns |
| POST | `/api/run` | Start an allowlisted `bot.py` command in a slot |
| POST | `/api/fanout` | Fan the same command out to several accounts |
| POST | `/api/stop` | Stop the running command of an account |
| GET | `/api/media/{id}/file` | Stream a media file with `Range` support |
| POST | `/api/media/upload` | Upload a file from the operator machine |
| GET | `/api/stream` | Live log stream (SSE) |

## Worker result

Completed queue rows store `result_summary` as a JSON object with `ok`, `account`, and
redacted `output`. The summary does not include tokens, session paths, or raw account names.
