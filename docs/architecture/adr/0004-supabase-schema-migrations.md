# ADR-0004: `supabase/schema.sql` stays single-file for now

Date: 2026-11-20. Status: Accepted (delays the "split supabase/schema.sql into versioned
migrations" item from the plan doc, with explicit trigger conditions).

## Context

`supabase/schema.sql` is applied manually to the Supabase project (README "Mini App control
plane", step 1). It covers mirror tables, command queue, events, leases, and RLS. The local
SQLite side already has a real migration path: `scripts/db-migrate.py` drives the add-only
`bot.migrate()` and explicitly documents that rollback is not supported.

## Decision

Keep the Supabase schema as one reviewed SQL file plus the existing documented manual apply
step. Do not build a migration framework now. Split into versioned migrations when any of
these triggers fire:

1. A schema change must be applied to a live Supabase project that cannot be recreated
   from scratch (production data with history that matters).
2. A second person or automation (CI) starts applying schema changes.
3. The file grows past ~500 lines or receives more than ~3 additive changes per month.

Until then, additive changes follow the same discipline as `bot.migrate()`: add-only
columns/tables, never destructive edits, RLS policy updates accompany table changes.

## Trade-offs

- Chosen over building versioned migrations now: the queue/mirror schema is small and
  stable, the operator is the single applier, and a migration runner would be unused
  machinery (violates the simplicity-first rule).
- Risk: manual apply steps can be forgotten or applied out of order. Mitigation: the apply
  step is documented in README's deployment checklist, and schema drift is visible through
  `scripts/healthcheck.py` / `env_check.py` output.

## Consequences

- When a trigger fires, the first migration (0001) must be the exact current content of
  `schema.sql` so history starts from a known-good baseline.
- Validation: `supabase/schema.sql` remains syntactically valid SQL and is the only
  authoritative cloud-schema artifact in the repo.