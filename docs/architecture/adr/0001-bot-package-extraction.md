# ADR-0001: Incremental extraction of `bot.py` into a package

Date: 2026-11-20. Status: Accepted (supersedes the vague "extract bot.py" item in
`docs/plan/mini-app-control-plane.md`).

## Context

`bot.py` is a 4768-line module with ~140 top-level functions/classes and 24 argparse
subcommands. It is the system of record for domain logic (accounts, groups, campaigns,
budgets, antispam, sessions). Consequences today: mypy cannot be meaningfully strict on it,
parallel work collides, and `web.py`/`control/executor.py` can only reuse it as a whole
process. Full-featured pytest suite (35 files, offline) already guards behavior.

## Decision

Strangler-style extraction into a new top-level package `tguserbot/`, staged so every step
keeps `py -3.15 -m pytest tests/ -q` green and `bot.py` remains the CLI entry point:

1. Pure helpers first (budget math, formatting, antispam tables, dedup keys) — no I/O.
2. SQLite persistence layer (`db.py`-style) — all connection/transaction handling.
3. Command handlers behind the existing argparse dispatch, one subcommand group at a time.
4. Telethon client/session management last (highest risk, live-account coupling).

Import rule stays as the project enforces today: imports at module top, no inline imports.

## Trade-offs

- Chosen over big-bang rewrite: each step is independently shippable and reversible;
  big-bang would freeze feature work for weeks and risk behavior drift in a
  real-account tool.
- Chosen against extracting directly into `control/`: control-plane code must stay free of
  Telethon domain logic; a separate `tguserbot/` keeps the trust boundary legible.
- Cost: temporary dual namespaces (`bot.py` re-exports extracted symbols) until a stage
  completes; mitigate with a single `bot.py` import block per extracted module.

## Consequences

- `pyproject.toml` gains `"tguserbot*"` in `packages.find` when stage 1 lands (see ADR-0003).
- `web.py` retirement (plan doc) stays gated on Mini App parity + rollback window; extraction
  is a precondition for that parity work, not a replacement for it.
- Validation per stage: pytest green, `ruff check .`, `py_compile` of touched files, and
  `python bot.py --help` unchanged output.