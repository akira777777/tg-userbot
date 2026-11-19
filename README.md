# tg-userbot

Telethon tooling for a **trusted personal Telegram account**: group discovery, nick scraping,
broadcasts, direct messages, an N-account orchestra, and an owner-only Telegram Mini App
control plane.

- **System of record** — local SQLite (`userbot.db`) and the Telethon `*.session` files.
- **Cloud** — Supabase stores a deliberately reduced operational mirror and the owner-only
  command queue; it never receives sessions, API hashes, or full recipient lists.
- **Interfaces** — `dashboard/` is the Next.js Mini App (deployed separately);
  `web.py` is a legacy panel that stays on `127.0.0.1`.

The repository does not contain a FastAPI service, a Redis application cache, or a
PostgreSQL application database.

## Repository layout

| Path | Purpose |
| --- | --- |
| `bot.py` | Userbot CLI: `login`, `groups`, `watch`, `scrape`, `broadcast`, `dmsend`, `conduct`, `media`, `privacy`, `align`, `compare`, `accounts`, `export`, `status`, `blacklist`, `stop`, `cloud`. |
| `cloud.py` | One-way push of the reduced mirror to Supabase. |
| `web.py` | Legacy loopback-only operator panel (`127.0.0.1:8642`) that starts `bot.py` subprocesses. |
| `control/` | Mini App control plane: Pydantic models, policy, Supabase queue, worker, subprocess executor, Bot API controller, `initData` verification. |
| `dashboard/` | Next.js Telegram Mini App: owner session, snapshot, command forms, timeline. |
| `supabase/schema.sql` | Mirror tables, command queue, events, leases, RLS. |
| `scripts/` | Operator utilities: `healthcheck.py`, `backup.py`, `db-migrate.py`, `db-seed.py`, `env_check.py`, `leave_junk_dialogs.py`. |
| `scripts/manual/` | Live smoke scripts (`panel_smoke.py`, `media_smoke.py`) that need a running panel and an authorized account. |
| `tests/` | pytest suite; each test uses a temporary SQLite database and never touches the network. |
| `docs/` | Architecture, API, media, Telegram, plan, and contribution guides. |
| `static/` | Legacy panel HTML served by `web.py`. |

## Local setup

Requires Python 3.12+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
copy .env.example .env
python bot.py login
```

Set `API_ID` and `API_HASH` from https://my.telegram.org before login. Treat `.env`,
`userbot.db`, and all `*.session*` files as secrets.

## Everyday commands

| Command | What it does |
| --- | --- |
| `python bot.py status` | Accounts, groups, campaigns, and today's budgets. |
| `python bot.py groups --sync` | Add every dialog to the broadcast list. |
| `python bot.py groups --probe` | Check write rights per account and drop read-only chats. |
| `python bot.py scrape` | Collect group members into the DM base. |
| `python bot.py broadcast --text "..." --dry-run` | Group broadcast preview. |
| `python bot.py dmsend --file message.txt --limit 10 --dry-run` | Direct-message preview. |
| `python bot.py conduct --what both --text "..." --dry-run` | Run one campaign across all accounts. |
| `python bot.py export` | Export SQLite tables to CSV. |
| `python cloud.py push` | Update the Supabase mirror. |

The full command reference, scenarios, and troubleshooting are in
[`РУКОВОДСТВО.md`](РУКОВОДСТВО.md) (Russian). Start with [`docs/GUIDE.md`](docs/GUIDE.md)
for the short operator path.

Direct-message automation can violate Telegram policy and can restrict or ban the account.
Start with dry runs and conservative limits.

## Mailing effectiveness

`broadcast` and `dmsend` treat `--text`/`--file` as a template rendered per target:

- placeholders `{name}`, `{first_name}`, `{full_name}`, `{username}`, `{chat}`, `{date}`;
  an empty value is removed together with its punctuation (`Привет, {name}!` -> `Привет!`);
- `[[variant 1|variant 2|variant 3]]` picks a random variant per target, so neighbouring
  chats do not receive byte-identical messages — the strongest mass-mailing signal;
- direct messages get a one-line opt-out footer (`--no-opt-out` disables it); `watch`
  turns a "stop" reply into a blacklist entry shared by every account;
- DM targets are sampled randomly from the whole base and interleaved by source group,
  so one batch is not twenty members of the same chat in a row;
- peers are addressed by `@username` when available (valid from any account), otherwise
  by the numeric id from the session cache — `access_hash` is per-account in Telegram, so
  it is never reused between accounts of the orchestra;
- a dropped connection is reconnected and the same target retried once;
- `status` shows a 7-day ok/fail/skip rate per channel.

Practical rules: keep `watch` running so opt-outs are honoured, send during the
recipients' daytime, grow budgets gradually on fresh accounts, and start every campaign
with `--dry-run` — it prints a few rendered previews (with the opt-out footer);
for `dmsend` no Telegram session or network is needed.

## Anti-spam policy

Telegram punishes in two different ways and the project reacts differently:
`FloodWait` is a technical rate limit (pause `seconds` + jitter, the target stays with
the same account), while `PEER_FLOOD` is an account-level spam restriction (24 h × 2^n
backoff up to 30 days, targets are never handed to another account).

- The daily DM cap is the **minimum** of the account budget and its cohort ceiling
  (fresh < 14 days: 15, warm < 90 days: 30, mature: 60, unknown: 20).
- `accounts.registered_at` is filled from Telegram (`User.bot_registered`) on
  `login`/`conduct`; `accounts.registered_at`, `cohort`, `spam_strikes` and
  `last_peerflood_at` are the anti-spam columns.
- `status` and `accounts list` print a risk line per account (cohort, age, `PEER_FLOOD`
  strikes).
- One account = one main MTProto session: never run two `conduct` workers on the same
  accounts (`AUTH_KEY_DUPLICATED` revokes authorization).

## Measuring delivery

`status='ok'` in `sent` only means the API accepted the call — not that a human read
it. The project records two hard signals:

- `sent.msg_id` is stored on every successful send (`send_run`, `conduct`, `conduct both`);
- `watch` catches incoming DMs and links them to the send (by `reply_to_msg_id`, else the
  latest DM to that person within 30 days), writing `sent.replied_at`; blocks are recorded
  in `sent.blocked_at`.

`python bot.py status` prints `доставка за 7 дней`: accepted / replied / blocked / silent
per channel, with `reply_rate` and `block_rate`.

`python bot.py report [--days 30] [--min-sample 10]` breaks the same figures down per
campaign and per collection source group (which audience actually answers), then prints a
verdict: `no_data` (sample too small), `stop_copy` (nobody replied — change the copy or
audience), `scale_winner` (scale that campaign only), `mixed` (difference within noise).
See [limits-and-antispam.md §8](docs/telegram/limits-and-antispam.md).

## Forwarding mode

`broadcast` and `dmsend` can forward prepared messages from a private source channel
instead of rendering `--text` per target:

```bash
python bot.py broadcast --from-source @secret_ads --msg-id 12 --dry-run
python bot.py dmsend --from-source @secret_ads --msg-id 12 --limit 10
```

`conduct` and `conduct both` accept the same flags; the source is resolved per
account (a private channel is visible only to its members), while budgets, pauses
and `PEER_FLOOD` handling stay with the conductor.

`--forward-from raw` (default) forwards with `drop_author=True`, so the source channel is
not shown in the chat; `self`/`--keep-author` keeps the forwarding header; `--no-preview`
disables link previews for text campaigns. Forwarding uses the same `sent` journal,
cross-account dedup, daily budgets and `PEER_FLOOD`/`FloodWait` handling as sending.

**It does not hide the author from moderation.** Reports carry chat context and
`messageFwdHeader` keeps the original `from_id`; the mode reduces how visible the source
is to people in the chat and lets you edit/delete the post after the campaign. The account
that presses forward is the one that carries the risk.

Background, server error codes and safe-budget heuristics:
[`docs/telegram/limits-and-antispam.md`](docs/telegram/limits-and-antispam.md).

## Mini App control plane

1. Apply `supabase/schema.sql` to the Supabase project.
2. Configure the control variables from `.env.example` with a random
   `CONTROL_SESSION_SECRET` of at least 32 characters.
3. Deploy `dashboard/` and set its server-side environment variables.
4. Start the trusted local processes:

```bash
python -m control main healthcheck
python -m control main worker
python -m control main telegram-bot
```

The worker must run where `userbot.db` and the authorized Telegram sessions are available.
The dashboard never receives session files or Telegram API hashes. See
[`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) for trust boundaries.

## Containers

The image runs the control worker by default. Persist both data and sessions:

```bash
docker compose up -d
```

`docker-compose.prod.yml` uses a prebuilt image. The dashboard remains a separate deployment.

## Verification

```bash
python -m pytest tests/
python -m ruff check .
python -m py_compile bot.py web.py cloud.py
cd dashboard && npm ci && npm test -- --run && npm run build
```

`python -m bandit -q -ll -r bot.py cloud.py web.py control scripts` is an optional extra
security pass.

## Documentation

- [`docs/GUIDE.md`](docs/GUIDE.md) — operator quick start.
- [`РУКОВОДСТВО.md`](РУКОВОДСТВО.md) — full Russian command reference.
- [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) — runtime model, trust boundaries, persistence.
- [`docs/api/README.md`](docs/api/README.md) — Mini App routes and command contract.
- [`docs/media/README.md`](docs/media/README.md) — media library over Saved Messages.
- [`docs/telegram/README.md`](docs/telegram/README.md) — Telethon, Bot API, and Mini App identity.
- [`docs/telegram/limits-and-antispam.md`](docs/telegram/limits-and-antispam.md) — server error codes, spam restriction model, backoff policy, safe budgets.
- [`docs/plan/mini-app-control-plane.md`](docs/plan/mini-app-control-plane.md) — design decisions and remaining work.
- [`docs/contributing/CONTRIBUTING.md`](docs/contributing/CONTRIBUTING.md) — local checks and review rules.

## Safety

The tool operates a real account: dry runs, budgets, flood pauses, and opt-out handling are
not optional decorations. Do not run live `broadcast`, `dmsend`, `conduct`, or `scrape`
just to test code — use the pytest suite and manual smoke scripts instead.
