# tg-userbot

Telethon-based tooling for a trusted personal Telegram account. The local SQLite database and Telegram session files are the system of record. Supabase stores an intentionally reduced operational mirror and the owner-only command queue; `dashboard/` is the Telegram Mini App frontend.

## Components

- `bot.py` — login, group discovery, scraping, broadcasts, direct messages, account orchestration, exports, and privacy operations.
- `web.py` — legacy loopback-only local panel. Keep it on `127.0.0.1`.
- `cloud.py` — push the reduced mirror to Supabase.
- `control/` — Telegram Bot API controller and trusted queue worker.
- `dashboard/` — Next.js Mini App deployed separately (for example on Vercel).
- `supabase/schema.sql` — mirror and command-queue schema.

The repository does not contain a FastAPI service, Redis application cache, or PostgreSQL application database.

## Local setup

Requires Python 3.12+.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
copy .env.example .env
python bot.py login
```

Set `API_ID` and `API_HASH` from https://my.telegram.org before login. Treat `.env`, `userbot.db`, and all `*.session*` files as secrets.

## Common commands

```bash
python bot.py groups --sync
python bot.py scrape
python bot.py broadcast --text "Preview" --dry-run
python bot.py dmsend --file message.txt --limit 10 --dry-run
python bot.py status
python bot.py export
python cloud.py push
```

Direct-message automation can violate Telegram policy and can restrict or ban the account. Start with dry runs and conservative limits.

## Mini App control plane

1. Apply `supabase/schema.sql` to the Supabase project.
2. Configure the control variables from `.env.example` with a random `CONTROL_SESSION_SECRET` of at least 32 characters.
3. Deploy `dashboard/` and set its server-side environment variables.
4. Start the trusted local processes:

```bash
python -m control.main worker
python -m control.main telegram-bot
```

The worker must run where `userbot.db` and authorized Telegram sessions are available. The dashboard never receives session files or Telegram API hashes.

## Containers

The image runs the control worker by default. Persist both data and sessions:

```bash
docker compose up -d
```

`docker-compose.prod.yml` uses a prebuilt image. The dashboard remains a separate deployment.

## Verification

```bash
python -m pytest tests/
python -m ruff check bot.py cloud.py web.py control tests scripts/healthcheck.py
cd dashboard && npm test -- --run && npm run build
```

See `docs/architecture/ARCHITECTURE.md` for trust boundaries and data flow.
