# Reconciliation Engine — Build Brief

> Working name: `recon_engine` (rename later). This file is the brief for Claude Code.
> Work phase by phase. Write tests first. Stop at the end of each phase for review.

## 1. What we're building

A deterministic Python engine that **proves** whether two business numbers agree, and if not, **explains every dollar of the difference** with evidence rows. It is called by AI agents (Claude, ChatGPT, Copilot) through an MCP server, but contains **no LLM itself**.

**Core rule: the AI proposes, the engine proves.**
- The AI finds sources, writes queries, forms hypotheses and explains results.
- The engine fetches data, matches rows, does the arithmetic, runs discrepancy tests, and issues a certificate.
- The AI never passes data values to the engine, only references and parameters.

This is NOT an app, NOT a Claude wrapper, and NOT an analytics/dashboard tool.

## 2. Non-negotiable principles

1. **No LLM calls anywhere in this repo.** Everything is deterministic and reproducible.
2. **The engine fetches its own data.** It uses its own credentials, which the AI never sees.
3. **Read-only.** SQL is parsed (sqlglot) and rejected unless it is a single SELECT. Connections should use read-only DB users.
4. **Everything is hashed.** Every extract records the source, the query text, query hash, row count, column totals, data hash and timestamp.
5. **Conservation.** Buckets + residual must equal the gap exactly. No row may be in two buckets.
6. **Honest residuals.** Anything not explained stays "unexplained". A certificate can be `RECONCILED` (residual = 0) or `OPEN`.
7. **Assumptions are explicit.** Every parameter that affects the result (filters, exclusions, date basis, key choice) is logged as an Assumption. Only a human can confirm one, via the CLI. Confirmation is never exposed via MCP, so the AI can't self-confirm.
8. **Guarded tests.** Each test has hard limits (e.g. timing window max ±1 period; a status test only counts rows whose status actually matches in the source).
9. **Versioned.** Certificates record the engine version and test versions.

## 3. Architecture

```
AI agent (any model)
   │  MCP tool calls (references + parameters only)
   ▼
MCP server  ──►  Engine core
                   ├── connectors/   (fetch + hash extracts)
                   ├── match/        (row-level matching, gap)
                   ├── tests/        (discrepancy test library)
                   ├── certify/      (conservation checks, certificate)
                   └── session/      (state for one reconciliation)
   ▼
Certificate (JSON + HTML) returned locally
   ▼ (later, optional, paid)
Verification service: receives hash + summary metadata only → signs → verify URL
```

Runs on the customer's machine or in their cloud. GitHub is only where the code is distributed from.

## 4. Repo structure

```
recon_engine/
  pyproject.toml          # uv, Python 3.11+
  src/recon_engine/
    models.py             # pydantic models (section 5)
    hashing.py            # canonical JSON + data hashing
    connectors/
      base.py
      files.py            # CSV / Excel uploads
      sql.py              # SQLAlchemy, SELECT-only via sqlglot
      powerbi.py          # Phase 5
    match.py
    tests/                # discrepancy tests (call them "checks" in code to avoid pytest clash)
      base.py             # Check interface + registry
      timing.py
      status_filter.py
      tax_basis.py
      duplicates.py
      sign_credit.py
    certify.py
    render.py             # certificate → HTML
    session.py
    cli.py                # typer: connections, confirm assumptions, view certs
    mcp_server.py         # Phase 4
  fixtures/
    generate.py           # synthetic datasets with injected discrepancies
  tests/                  # pytest
```

## 5. Data model (pydantic)

- **Connection**: id, type (file/sql/powerbi), config (credentials stored locally, never returned)
- **Extract**: id, connection_id, query_text, query_hash, row_count, column_totals, data_hash, fetched_at
- **MatchSpec**: extract_a, extract_b, key columns, amount column, date column(s), period
- **MatchResult**: matched_count, only_a rows, only_b rows, amount_differences, gap
- **Assumption**: id, description, parameter, value, proposed_by (ai/human), confirmed (bool), confirmed_by, confirmed_at
- **Bucket**: check_name, check_version, parameters, row_ids, amount, row_count, assumptions used
- **Certificate**: id, engine_version, status (RECONCILED/OPEN), extracts (with hashes), match_spec, buckets, residual, unconfirmed_assumptions, created_at, certificate_hash

## 6. Check interface

Every check:
- takes the unmatched rows plus the extracts it's allowed to read, and validated parameters
- enforces its own guardrails (rejects out-of-range parameters)
- returns a Bucket containing **only** rows it can prove, with the amount summed by the engine
- never claims rows already claimed by another bucket
- returns an empty bucket (not an error) when the hypothesis is wrong

## 7. Phases

### Phase 0: Scaffold
uv project, pytest, ruff, pydantic, pandas, sqlalchemy, sqlglot, typer.
**Done when:** `uv run pytest` passes on an empty test suite.

### Phase 1: Extracts + matching
- File connector (CSV/XLSX) and SQL connector (SQLite for tests, then SQL Server / Postgres)
- SELECT-only validation; reject anything else with a clear error
- Hashing of query and data; column totals
- `match()` returns MatchResult and gap

**Done when:** tests show an identical pair reconciles with gap 0, a non-SELECT query is rejected, and the same data always gives the same hash.

### Phase 2: First five checks
timing, status_filter, tax_basis (inc vs ex GST), duplicates, sign_credit (sign flips / credit notes).

Build `fixtures/generate.py` first. It should generate a "dashboard vs Xero" pair with known injected issues, e.g.:
- 58 invoices with appointment date in Oct and invoice date in Nov (timing, $26,900)
- 3 voided invoices included on one side only (status, $600)
- plus GST, duplicate and credit-note cases

**Done when:** each check finds exactly its injected rows, wrong hypotheses return empty buckets, guardrails reject out-of-range parameters, and no row lands in two buckets.

### Phase 3: Certificate
- `certify()` enforces conservation and returns RECONCILED or OPEN
- Canonical JSON plus certificate hash
- HTML render that a CFO can read: headline status, gap, buckets with amounts and counts, assumptions (confirmed vs unconfirmed), source and query details, residual
- CLI: `recon confirm <assumption_id>`, `recon cert show <id>`

**Done when:** the fixture case produces a RECONCILED certificate with residual 0, and a certificate with unconfirmed assumptions is clearly flagged.

### Phase 4: MCP server
Python MCP SDK (FastMCP). Tools:
- `list_connections`, `profile_source`, `run_query` (returns an extract summary, not raw credentials)
- `get_rows` (paged, for the AI to inspect unmatched rows)
- `match`, `list_checks`, `run_check`
- `propose_assumption` (AI can propose; only the CLI confirms)
- `certify`, `get_certificate`

**Done when:** Claude Code, connected to the MCP server, can reconcile the fixture case end to end from a plain-English prompt, and every number in its explanation traces to the engine.

### Phase 5: Power BI connector
Run DAX queries against a semantic model (Power BI REST `executeQueries` or XMLA), treated as an extract like any other.

**Done when:** a measure value from a Power BI model can be reconciled against a SQL source.

### Phase 6 (later): Verification service
Signs certificate hashes (Ed25519) and serves a verify page. It only ever receives hashes and summary metadata, never row data. Start with local key signing, then a minimal hosted registry.

## 8. Later checks backlog
Grain mismatch (line vs header), mapping/coding differences, missing records, manual journals/accruals, FX, rounding, scope (site/entity/intercompany), timezone cut-off (UTC vs local, e.g. AEST/AEDT).

## 9. Out of scope
LLM calls, dashboards/analytics, writing to any source system, a desktop app UI, pre-built system-specific integrations (Cliniko, Xero etc.) beyond generic SQL/file/Power BI.

## 10. Licence
Source-available (BSL or AGPL + commercial dual licence). Decision pending. Add a placeholder LICENSE.
