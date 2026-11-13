# recon_engine

A deterministic Python engine that proves whether two business numbers agree,
and if not, explains every dollar of the difference with evidence rows.
AI agents call it through an MCP server; the engine itself contains no LLM.

See [CLAUDE.md](CLAUDE.md) for the full build brief.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The supported floor is Python 3.11. To run the suite on it as well:

```bash
uv run --python 3.11 pytest
```

**Windows path length.** Compiled dependencies (numpy, SQLAlchemy) fail to load
with "The filename or extension is too long" if the virtualenv sits under a
deep path. Keep the checkout at a short path such as `C:\dev\recon_engine`,
econ_engine`,
or point `UV_PROJECT_ENVIRONMENT` at a short directory.

## Layout

- `hashing.py`: canonical values and JSON, query and data hashes, column totals
- `models.py`: pydantic models (Connection, Extract, MatchSpec, MatchResult, ...)
- `connectors/`: `files.py` (CSV/XLSX) and `sql.py` (SQLAlchemy, SELECT-only via sqlglot)
- `match.py`: row-level matching of two extracts and the gap
- `checks/`: discrepancy checks (timing, status_filter, tax_basis, duplicates,
  sign_credit, rounding) behind one interface and registry; the engine sums each
  bucket and refuses overlapping claims
- `fixtures/generate.py`: deterministic "dashboard vs Xero" pair with injected
  issues; output and answer key live in `fixtures/data/`

- `fixtures/generate_messy.py`: a messy pair (case/whitespace variants, blanks,
  compound timing+GST, near-duplicates, 1-cent rounding) whose honest residual is
  not zero; its manifest lists every item that must stay unexplained
- `assumptions.py`: the assumption store; buckets and certificates refer to it by ID.
  Like the connection store, it is read from disk on every call, so a confirmation
  made in a terminal reaches a running MCP server at once
- `certify.py`: conservation checks, certificate issue and hash verification
- `render.py`: certificate to a printable HTML report
- `session.py`: the on-disk workspace (`.recon/` or `$RECON_HOME`) and a reconciliation session

Regenerate the fixtures with:

```bash
uv run python fixtures/generate.py
```

```bash
uv run python fixtures/generate_messy.py
```

## MCP server (for AI agents)

The engine is driven by an AI through an MCP server; the engine itself makes no
LLM calls. Tools take IDs and parameters only and return engine-computed numbers:

`list_connections`, `profile_source`, `run_query`, `get_rows`, `match`,
`list_open_items`, `list_checks`, `run_check`, `propose_assumption`, `certify`,
`get_certificate`.

There is deliberately no tool to confirm an assumption. A certificate that
balances on unconfirmed assumptions is PROVISIONAL until a person confirms them.

1. Add data sources (credentials stay in `.recon/connections.json`, never sent to the AI):

```bash
uv run recon connections add-file dashboard fixtures/data/dashboard_oct2026.csv
```

```bash
uv run recon connections add-sql ledger
```

2. Claude Code picks up the server from `.mcp.json` in this folder (approve it when
   prompted). Other clients can run it directly:

```bash
uv run recon-mcp
```

Built on the Python MCP SDK 2.x, where FastMCP is now called `MCPServer`.

## Assumptions

Every claim a check makes rests on assumptions, and each one is scoped to the
records it covers: "For these 12 records (INV-10402 … INV-10413), xero amounts
include 10% tax and dashboard amounts exclude it." The covered keys are part of
the assumption's ID, so the same reasoning applied to other records is a new
assumption that needs its own confirmation. A check that proves nothing assumes
nothing.

An assumption is **elevated** when its parameter loosens a check's default, and
it carries a one-line reason. Elevated assumptions head the HTML certificate and
`recon assumptions list`:

- duplicates with `compare_columns` narrower than every column (and it can never
  leave out the match key, amount or date columns)
- status_filter naming several spellings of one status (`VOIDED`, `Voided`)
- timing where a claimed row's actual lag exceeds 7 days (the window itself may be
  up to one period; the assumption reports the largest lag found)
- rounding (any tolerance, default $0.01, at most $0.05 per record) and
  tax_basis with a `rounding_tolerance` (at most $0.01)

Everything a person reads calls each side by its connection name, not "side A/B".

## File sources are hashed when registered

`recon connections add-file` records the file's SHA-256, size and modified time,
and who registered it. Every read hashes the bytes it parses and compares. If the
file has changed since (or was added before hashing existed), the extract carries
a warning naming the file and no certificate built on it can be better than OPEN.
After checking the file, a person re-registers it, interactively, with:

```bash
uv run recon connections refresh <connection_id>
```

## SQL must not truncate, and its filters are assumptions

A SQL extract must return every row it selects: `LIMIT`, `TOP`, `OFFSET`,
`FETCH`, `SAMPLE` and `TABLESAMPLE` are refused. Every other way a query leaves
rows out (each AND-ed part of a `WHERE` or `HAVING`, each `JOIN`, `DISTINCT`, a
de-duplicating `UNION`, `EXCEPT`, `INTERSECT`) is recorded on the extract and
becomes an assumption, worded in plain English with the SQL fragment, as soon as
the extract is matched. A `WHERE` that only limits rows to the reconciliation
period is a normal assumption; anything else is elevated. Either way a person
must confirm it before the certificate can be RECONCILED.

## Confirming assumptions (people only)

`recon confirm` runs only in an interactive terminal and asks you to retype a
random code, so scripts and AI agents cannot drive it. This project's
`.claude/settings.json` also denies the command to Claude Code.

## CLI

Confirming an assumption is a human-only action and exists only here:

```bash
uv run recon assumptions list
```

```bash
uv run recon confirm <assumption_id> --by "Your Name"
```

```bash
uv run recon cert list
```

```bash
uv run recon cert show <certificate_id> --html report.html
```
