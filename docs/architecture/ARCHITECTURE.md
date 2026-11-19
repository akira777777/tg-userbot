# Architecture

## Runtime model

The trusted machine is the security boundary. `bot.py` uses Telethon sessions and SQLite
directly; there is no remote API with access to Telegram credentials.

```text
Telegram MTProto <-> bot.py <-> userbot.db
                         |-> sessions/*.session
                         |-> cloud.py -> Supabase mirror

Telegram Mini App -> dashboard server routes -> Supabase command queue
                                                   |
                                      control/worker.py
                                                   |
                                      control/executor.py -> bot.py
```

## Module responsibilities

- `bot.py` — userbot domain operations, account selection, throttling, sending, local persistence.
- `web.py` — legacy loopback-only operator panel that starts `bot.py` subprocesses (`127.0.0.1:8642`).
- `cloud.py` — one-way synchronization of reduced operational data.
- `control/` — authenticated owner command models, policy, queue repository, worker,
  subprocess adapter, Bot API controller, and `initData` verification.
- `dashboard/` — Telegram `initData` authentication, signed session cookie, read model,
  and command submission UI.
- `supabase/schema.sql` — mirror tables, command queue, events, leases, and RLS.
- `scripts/` — healthcheck, backup, migrations, one-off operator utilities;
  `scripts/manual/` holds live smoke scripts that require a running panel.
- `tests/` — pytest suite; every test uses a temporary database and never touches the network.

## Repository layout

```text
tg-userbot/
├── bot.py, web.py, cloud.py     entry points
├── control/                     Mini App control plane
├── dashboard/                   Next.js Mini App
├── supabase/schema.sql          mirror + command queue
├── scripts/                     ops utilities, manual/ smoke scripts
├── tests/                       pytest
├── docs/                        architecture, api, media, telegram, plan, contributing
├── static/                      legacy panel HTML
├── Dockerfile, docker-compose*.yml
└── README.md, РУКОВОДСТВО.md
```

## Trust boundaries

1. `.env`, `userbot.db`, and Telegram session files stay on the trusted worker host.
2. The dashboard holds a server-side Supabase credential and never exposes it to browser bundles.
3. Browser requests require verified Telegram `initData` and an owner allowlist.
4. LIVE commands require a confirmation nonce bound to the request digest.
5. The worker validates every claimed command again before execution.
6. Cloud synchronization excludes collected member records and minimizes account metadata.
7. Command output stored in the queue is redacted: no tokens, session paths, or raw recipient IDs.

## Persistence and concurrency

SQLite is the local source of truth and uses WAL plus a busy timeout for concurrent readers.
Supabase is a telemetry mirror and remote command transport, not a replacement for SQLite.
Queue claims use leases so only one worker owns a command; long-running commands renew their
lease until completion. `conduct` keeps one campaign across accounts and respects per-account
budgets, flood pauses, and cross-account deduplication.

## Deployment

- Local CLI: `python bot.py <command>`.
- Worker: `python -m control main worker`.
- Controller: `python -m control main telegram-bot`.
- Dashboard: deploy `dashboard/` separately.
- Container: the production image defaults to the worker and requires persistent
  `/app/data` and `/app/sessions` volumes.

Redis, FastAPI, and a separate PostgreSQL application database are not part of this
architecture. See [`../plan/mini-app-control-plane.md`](../plan/mini-app-control-plane.md)
for the design rationale.
