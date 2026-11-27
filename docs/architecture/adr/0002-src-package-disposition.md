# ADR-0002: Disposition of the orphan `src/` package

Date: 2026-11-20. Status: Accepted.

## Context

`src/` (`booster.py`, `poster.py`, `rate_limiter.py`, `scheduler_core.py`, `main.py`) is a
self-contained delivery-stack library: Poster → RateLimiter → window-budget Booster, with an
injectable clock and its own offline tests (`src/test/`, wired into pytest via
`testpaths = ["tests", "src/test"]`).

Facts verified 2026-11-20: zero imports from product code (`bot.py`, `cloud.py`, `web.py`,
`control/` — the earlier `rg 'src'` hits are local variables, not imports). It is **not**
packaged (`py-modules = ["bot","cloud","web"]`, `packages.find include = ["control*"]`).

## Decision

Keep `src/` as the **seed for stage 3 of ADR-0001** (sending/rate/budget stack), do not
delete it, do not package it yet. When extraction reaches the send-loop stage, the Booster/
RateLimiter/Budget pieces move under `tguserbot/` (package `tguserbot.delivery` or similar),
their tests move to `tests/`, and `testpaths` drops `src/test`.

## Trade-offs

- Chosen over deleting now: the library is tested, dependency-free, and directly reusable
  for the hardest part of the extraction; deletion would discard a verified abstraction.
- Chosen over wiring it into `bot.py` today: touching the live sending path without a staged
  plan violates the incremental-safety rule of ADR-0001.
- Cost: ~6 files of dead weight until integration; accepted because they are isolated,
  lint-clean, and clearly documented here.

## Consequences

- `src/` must not grow new product features; it stays a frozen seed.
- Any change to budgets/rate limiting in `bot.py` should prefer the `src/` semantics so the
  eventual merge is mechanical.
- Validation: `rg 'booster|rate_limiter|scheduler_core'` outside `src/` returns nothing
  until stage 3 lands; pytest including `src/test` stays green.