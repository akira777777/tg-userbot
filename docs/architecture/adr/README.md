# ADR index — docs/architecture/adr/

Numbered, append-only decision records. One decision per file; supersede, never edit history.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-bot-package-extraction.md) | Incremental extraction of `bot.py` into a package | Accepted |
| [0002](0002-src-package-disposition.md) | Disposition of the orphan `src/` package | Accepted |
| [0003](0003-single-packaging-source.md) | `pyproject.toml` as the single packaging source | Accepted |
| [0004](0004-supabase-schema-migrations.md) | `supabase/schema.sql` stays single-file for now | Accepted |

Rules: significant or hard-to-reverse decisions get an ADR; routine reversible changes do not.
Every ADR states context, decision, trade-offs, and how to validate it.