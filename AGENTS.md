## Learned User Preferences

- Prefers short Russian commands (`проверь`, `полная проверка`, `debug`, `improve`/`улучшай`, `запусти` / `запусти панель`) and concise Russian replies.
- `полная проверка` means local pytest, ruff, compile, dashboard tests, and health/env checks — not live campaigns. Do not run live `broadcast`/`dmsend`/`conduct`/`scrape` unless explicitly asked; panel_smoke dry API is allowed.
- `запусти`, `запусти панель`, and `запусти проект` start local `web.py`; do not kill an already-running panel.
- `рассылка` means group broadcast (not DMs). When sourcing copy from another account (e.g. last message in `main` Saved Messages), hide forward origin via existing privacy/hide-forward modes and send from all accounts except the source.
- Requests to find/join groups or equalize buffet/batch accounts mean engaging all accounts with equal group coverage and higher DM capacity, not a one-off scrape of a single chat.
- When asked to speed campaigns (`быстрее круги`), keep per-message pauses unchanged; speed up via overlapping session/dialog warmup and background coverage refresh.
- Enforces no inline imports: keep imports at module top unless a documented circular-dependency exception exists (explicitly corrected for `SlotLocker` in `bot.py`).
- When `improve`/`улучшай` is underspecified and the user replies `.`, proceed through remaining high-priority findings from the latest audit instead of re-asking for scope.
- Never commit Telegram tokens, session files, recipient/group ID dumps, or debug logs with operational campaign data. User-facing docs should be in Russian.

## Learned Workspace Facts

- This directory is the product Telethon userbot (CLI `bot.py`, control plane `python -m control.main`, Next.js Mini App under `dashboard/`, SQLite `userbot.db`, panel `web.py` at `http://127.0.0.1:8642`, Supabase-backed control queue/schema). The parent Desktop `tg-userbot/` wrapper may be a separate empty git repo with a different remote — run git from this tree.
- Treat root `README.md` and `docs/architecture/` as authoritative; several other `docs/*` guides describe a different FastAPI/Redis/Alembic stack that is not this codebase.
- `bot.py` loads only a sibling `.env`; `control.config` now loads the same repo `.env` (`USERBOT_ENV` override) with `override=False`, so CLI and control no longer disagree via cwd/parent dotenv discovery.
- Mini App owner access requires a signed `tg_control_session` cookie whose Telegram id is in `OWNER_TELEGRAM_IDS`; control routes must not trust an unauthenticated fallback owner id.
- Session files are guarded with `control.slots.SlotLocker`; panel/worker children set `USERBOT_SLOT_HELD` so nested `bot.py` does not re-lock the same slot. A live `watch` holding a slot can make panel dry broadcast return 409. On `get_client` failure, release only that account’s slot — never all process locks.
- Stored `access_hash` values are intentionally not reconstructed into Telethon peers for DM sends; delivery uses bare `user_id`.
- Local interpreter for verification is `py -3.15`. `dashboard/AGENTS.md` holds auto-generated Next.js agent rules; durable project memory belongs in this `AGENTS.md`.
- Panel launch on Windows: `scripts/start_panel.ps1` (PowerShell; checks port 8642 / `WEBBOT_PORT` first, prints already-running and exits instead of duplicating) plus Makefile target `panel`. Direct background shells must use PowerShell — the default POSIX shell fails with ENOENT on this machine.
- Import ready Telethon `.session` files with `py -3.15 bot.py accounts import <name> <path>` into `sessions/`; new workers continue sequential `workN` names after existing ones, and unauthorized imports should be disabled.
- Campaign conductor overlaps up to three account session/dialog warmups (first coverage map still gates start; later refreshes are background); SQLite uses WAL with `synchronous=NORMAL`; panel keeps a local status snapshot for about 10s.
