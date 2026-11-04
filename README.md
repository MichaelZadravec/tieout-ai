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
  sign_credit) behind one interface and registry; the engine sums each bucket and
  refuses overlapping claims
- `fixtures/generate.py`: deterministic "dashboard vs Xero" pair with injected
  issues; output and answer key live in `fixtures/data/`

- `fixtures/generate_messy.py`: a messy pair (case/whitespace variants, blanks,
  compound timing+GST, near-duplicates, 1-cent rounding) whose honest residual is
  not zero; its manifest lists every item that must stay unexplained
- `assumptions.py`: the assumption store; buckets and certificates refer to it by ID
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
