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
