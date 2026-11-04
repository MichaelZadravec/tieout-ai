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
