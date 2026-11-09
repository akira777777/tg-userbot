# API reference

The project does not expose a public FastAPI or JWT API. Remote control goes through the Next.js Mini App, and the legacy panel stays on localhost.

## Mini App server routes

All `/api/control/*` routes require the signed owner session created from Telegram `initData`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/control/session` | Verify Telegram `initData` and set the session cookie |
| GET | `/api/control/bootstrap` | Read the reduced Supabase snapshot |
| POST | `/api/control/confirmations` | Create a five-minute confirmation for a LIVE delivery |
| POST | `/api/control/commands` | Enqueue a supported command |
| GET | `/api/control/commands` | List the owner’s commands |
| GET | `/api/control/commands/{id}` | Read one command |

Supported command kinds are `STATUS`, `GROUPS`, `CAMPAIGNS`, `MEDIA_LIST`, `BLACKLIST_ADD`, `BROADCAST`, `DMSEND`, `CONDUCT`, and `STOP`. Read-only kinds require mode `READ`. Delivery kinds require `DRY_RUN` or `LIVE`; `LIVE` also requires the confirmation nonce. Unsupported kinds are rejected before enqueue.

`DRY_RUN` always passes `--dry-run` to `bot.py`. `LIVE` omits it and can send Telegram messages.

## Local panel

`web.py` listens on `127.0.0.1:8642`. `POST /api/run`, `/api/fanout`, `/api/stop`, and `/api/media/upload` require header `X-Web-Token` matching `WEB_TOKEN` (at least 16 characters). When the token is absent, those writes return HTTP 503.

## Worker result

Completed queue rows store `result_summary` as a JSON object with `ok`, `account`, and redacted `output`. The summary does not include tokens, session paths, or raw account names.
