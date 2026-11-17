# recon_engine

A deterministic Python engine that proves whether two business numbers agree,
and if not, explains every dollar of the difference with evidence rows.
AI agents call it through an MCP server; the engine itself contains no LLM.

See [CLAUDE.md](CLAUDE.md) for the full build brief.
See [SECURITY.md](SECURITY.md) for the threat model, what the engine catches and flags,
and red-team results including what is still open.

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

## Setting up a folder for a customer

Install the engine as a tool, put it on PATH, then run `recon init` in an empty
folder:

```bash
uv tool install --python 3.14 C:\path\to\recon_engine
```

```bash
uv tool update-shell
```

```bash
recon init
```

`recon init` creates the `.recon/` workspace, registers the MCP server in
`.mcp.json` (`recon-mcp --home .recon`), and writes `.claude/settings.json` that
allows the recon tools but denies `recon confirm`, `recon keys` and
`recon connections` (in Bash and PowerShell) and any edit inside `.recon/`. It
checks that `recon` and `recon-mcp` are on PATH and says how to fix it if not,
creates your confirmer key if there is none, and prints the next steps. It
merges into existing files and is safe to run again: a second run changes
nothing.

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

## Connections are signed by the person who registers them

`recon connections add-file`, `add-sql` and `refresh` run only in an interactive
terminal and ask for your confirmer key's passphrase. Each signs the whole
connection record (ID, type, path or URL, and the file's hash). The engine reads
only through connections whose record verifies against this machine's confirmer
key, so a connection added to `connections.json` by hand, or a registration
rewritten there, is refused. `recon connections list` and `list_connections`
say which connections are signed.

## SQL must not truncate, and its filters are assumptions

A SQL extract must return every row it selects: `LIMIT`, `TOP`, `OFFSET`,
`FETCH`, `SAMPLE` and `TABLESAMPLE` are refused. Every other way a query leaves
rows out (each AND-ed part of a `WHERE` or `HAVING`, each `JOIN`, `DISTINCT`, a
de-duplicating `UNION`, `EXCEPT`, `INTERSECT`) is recorded on the extract and
becomes an assumption, worded in plain English with the SQL fragment, as soon as
the extract is matched. A `WHERE` that only limits rows to the reconciliation
period is a normal assumption; anything else is elevated. Either way a person
must confirm it before the certificate can be RECONCILED.

An output column the query computes instead of reading (a `CASE`, arithmetic, an
aggregate) is an elevated assumption too, even when no row is left out, and the
key, amount or date column it feeds is named. A SQLite database is hashed like a
file (with its `-wal` log) when a person registers it, so editing it is caught.

## Confirming assumptions (people only)

A confirmation counts only if it is signed with the confirmer's key. Create your
key once (it asks for a passphrase; the private key is stored encrypted in your
user profile, `%USERPROFILE%\.recon\keys` on Windows, never in the workspace):

```bash
uv run recon keys init --name "Your Name"
```

`recon confirm` runs only in an interactive terminal, asks you to retype a
random code and then for your passphrase, and signs the assumption's ID, a hash
of its content, your name and the time. `certify` verifies every confirmation
against your public key: one that is unsigned (for example `"confirmed": true`
typed into the store), signed by another key, or whose assumption was edited
after signing counts as unconfirmed, and the certificate says why. Certificates
record the key's fingerprint and owner and show it prominently as the *expected*
confirmer key. Anyone checking a certificate should compare it with what
`recon keys fingerprint` prints on the confirmer's own machine: a certificate checked
against any other key is not theirs.
This project's `.claude/settings.json` also denies these commands to Claude Code.

## Signing a certificate off

A RECONCILED certificate is balanced and rests only on confirmed assumptions, but it
is not final until a person signs it off:

```bash
uv run recon cert sign <certificate_id>
```

Like `recon confirm`, this runs only in an interactive terminal, asks you to retype a
code and then for your passphrase, and signs the certificate's ID, hash and status
with your key. The sign-off is stored beside the certificate (it cannot go inside it
without changing the hash it signs). `recon cert show`, `get_certificate` and the HTML
say whether a certificate is signed off; a hand-written, moved or foreign-key sign-off
does not count.

## CLI

Confirming an assumption is a human-only action and exists only here:

```bash
uv run recon assumptions list
```

```bash
uv run recon confirm <assumption_id>
```

```bash
uv run recon cert list
```

```bash
uv run recon cert show <certificate_id> --html report.html
```
