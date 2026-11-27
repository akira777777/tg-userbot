# ADR-0003: `pyproject.toml` as the single packaging source

Date: 2026-11-20. Status: Accepted (closes the "choose one packaging/lockfile strategy"
item from `docs/plan/mini-app-control-plane.md`).

## Context

The repo previously had drift: `requirements.txt`, `pyproject.toml`, and three different
interpreter stories in docs (`РУКОВОДСТВО.md` created a venv with `py -3.14` although no
`.venv` exists in the tree; README says Python 3.12+; the local verification interpreter is
`py -3.15`).

`requirements.txt` already declares itself a runtime mirror ("Источник правды —
pyproject.toml") for environments where editable install is unavailable.

## Decision

1. `pyproject.toml` is the single source of truth for dependencies, entry points
   (`tg-userbot`, `tg-userbot-cloud`, `tg-userbot-control`), and tool config.
2. `requirements.txt` stays as a no-edit mirror; it may only change when `pyproject.toml`
   changes, never independently.
3. No lockfile: this is a single-operator, single-machine system; pinning adds staleness
   risk without a reproducibility win. Revisit only if a second deployment host appears.
4. Interpreter floor: `requires-python = ">=3.12"`; local verification interpreter is
   `py -3.15` (documented in `AGENTS.md` and README's verification section).
5. When ADR-0001 stage 1 lands, add `"tguserbot*"` to `packages.find.include`.

## Trade-offs

- No lockfile = builds can drift between pip releases; accepted because the operator
  controls the only runtime and upgrades are manual and rare. A `requirements.lock`
  would be justified the moment a CI deployment host is added.
- Mirror file duplication is a known footgun; mitigated by the "mirror only" comment
  header and this ADR.

## Consequences

- Operator docs must not introduce a third install path. `РУКОВОДСТВО.md` section 1 is
  synced to this decision (venv creation uses `py -3.15`, install uses `pip install -e .`).
- Validation: `pip install -e ".[dev]"` succeeds; `python bot.py --help` works without
  `requirements.txt` present.