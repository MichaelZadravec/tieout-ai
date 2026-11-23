# AI TieOut — Brief for Claude Code

> Product: **AI TieOut**. Package `tieout-ai` (import `tieout_ai`), CLI `tieout` (also
> installed as `tieout-ai`, so `uvx tieout-ai ...` works), MCP server `tieout-mcp`.
> Licence: Apache-2.0. This file is the brief for Claude Code working on this repo.
> Write tests first. Stop for review at the end of each piece of work.

## 1. What this is

A deterministic Python engine that **proves** whether two business numbers agree, and if
not, **explains every dollar of the difference** with evidence rows. AI agents (Claude,
ChatGPT, Copilot) drive it through an MCP server, but it contains **no LLM itself**.

**Core rule: the AI proposes, code proves, people confirm.**
- The AI finds sources, writes queries, forms hypotheses and explains results.
- The engine fetches data, matches rows, does the arithmetic, runs the checks, and
  issues a certificate.
- A person confirms the judgement calls and signs certificates off.
- The AI never passes data values to the engine, only references and parameters.

This is NOT an app, NOT a Claude wrapper, NOT an analytics/dashboard tool, and there is
no hosted or paid service. Everything runs on the user's machine or in their cloud.

## 2. Non-negotiable principles

1. **No LLM calls anywhere in this repo.** Everything is deterministic and reproducible.
2. **The engine fetches its own data.** It uses its own credentials, which the AI never sees.
3. **Read-only.** SQL is parsed (sqlglot) and rejected unless it is a single SELECT.
   Connections should use read-only DB users.
4. **Everything is hashed.** Every extract records the source, the query text, query
   hash, row count, column totals, data hash and timestamp.
5. **Conservation.** Buckets + residual must equal the gap exactly. No row may be in two
   buckets.
6. **Honest residuals.** Anything not explained stays "unexplained". A certificate is
   `OPEN` (something unexplained), `PROVISIONAL` (all explained, some assumptions
   unconfirmed) or `RECONCILED` (all explained and confirmed).
7. **Assumptions are explicit.** Every parameter that affects the result (filters,
   exclusions, date basis, key choice) is logged as an assumption, scoped to the records
   it covers. Only a person can confirm one, via the CLI; never via MCP.
8. **Guarded checks.** Each check has hard limits (e.g. timing window at most one
   period; a status check only counts rows whose status actually matches in the source).
9. **Versioned.** Certificates record the engine version and check versions.
10. **The on-disk stores are the only source of truth.** Never cache them across calls.
11. **Loosening is elevated.** Any parameter or filter that loosens a default must
    produce an elevated assumption with a one-line reason.
12. **Sources are what was registered.** File and SQLite sources must match their
    registered hash; SQL must not truncate; registrations are signed.
13. **Signed confirmations.** Confirmations are valid only if signed with the
    confirmer's key.
14. **Humans register, refresh, confirm and sign off.** Anything that does so is
    human-only and interactive, and is never reachable from the MCP server.
15. **Final means signed off.** A RECONCILED certificate is final only once a person
    signs it off. Stored certificates must be self-consistent to load.

## 3. Architecture

```
AI agent (any model)
   │  MCP tool calls (references + parameters only)
   ▼
MCP server (tieout-mcp)  ──►  Engine core
                                ├── connectors/   (fetch, hash and check sources)
                                ├── match         (row-level matching, gap)
                                ├── checks/       (discrepancy check library)
                                ├── certify       (conservation, certificates, sign-off checks)
                                └── session       (workspace and one reconciliation)
   ▼
Certificate (JSON + HTML), stored locally in the workspace

Person, in their own terminal (tieout CLI):
  register sources · confirm assumptions · sign certificates off   (signed with their key)
```

The workspace is `.tieout/` in the working folder (or `$TIEOUT_HOME`). The confirmer's
key is in `~/.tieout/keys` (or `$TIEOUT_KEYS_DIR`); a key from the earlier
`~/.recon/keys` is copied there once, automatically.

## 4. Repo structure

```
pyproject.toml            # uv, Python 3.11+
src/tieout_ai/
  models.py               # pydantic models (section 5)
  hashing.py              # canonical JSON, data and file hashing
  connectors/             # base.py, files.py (CSV/Excel), sql.py (SELECT-only, filters)
  match.py
  checks/                 # timing, status_filter, tax_basis, duplicates, sign_credit, rounding
    base.py               # Check interface, Proposal, registry
  assumptions.py          # assumption store
  connections.py          # connection store, signed registrations
  signing.py              # confirmer keys, signed confirmations and records
  certify.py              # conservation, certificates, self-consistency, sign-off checks
  session.py              # workspace and one reconciliation
  service.py              # what the MCP server exposes
  mcp_server.py
  cli.py                  # tieout: init, demo, connections, confirm, cert, keys
  project.py              # tieout init: workspace, .mcp.json, Claude Code permissions
  render.py               # certificate -> HTML
  demo.py                 # tieout demo
  fixtures/               # deterministic clean and messy "dashboard vs Xero" pairs
fixtures/                 # committed fixture data + regeneration entry points
tests/                    # pytest
ROADMAP.md                # what's done, now, next, later and out of scope
ai-reasoning-test/        # reproducible test of how an AI chat reasons about a gap
.github/workflows/        # CI (3 OSes x Python 3.11/3.14) and PyPI publishing
```

## 5. Data model (pydantic)

- **Connection**: id, type (file/sql/powerbi), config (path or URL, registration hash,
  signature; stored locally, never returned to the AI)
- **Extract**: id, connection_id, query_text, query_hash, columns, row_count,
  column_totals, data_hash, fetched_at, changed_since_registration, source_warning,
  query_filters, computed_columns
- **MatchSpec**: extract_a, extract_b, key columns, amount column, date column(s), period
- **MatchResult**: matched_count, only_a / only_b rows, amount_differences, excluded and
  invalid rows, totals, gap
- **Assumption**: id, description, parameter, value, scope, elevated, elevated_reason,
  proposed_by, confirmed, confirmed_by, confirmed_at, signature fields
- **Bucket**: check_name, check_version, parameters, item_ids, row_ids, amount,
  row_count, assumption_ids
- **Certificate**: id, engine_version, status, extracts, match_spec, buckets, residual,
  gross_unexplained, residual_items, assumptions, unconfirmed and unverified
  confirmations, confirmer key fingerprint, source_warnings, supersedes, certificate_hash.
  A sign-off is stored beside it, in `certificates/signoffs/`.

## 6. Check interface

Every check (see CONTRIBUTING.md, "Write your own check"):
- has a `Params` model whose guardrails reject out-of-range parameters
- `prove()`s only the open items it can prove; the engine sums the amounts
- never claims items already claimed by another bucket (the engine refuses it)
- returns an empty bucket (not an error) when the hypothesis is wrong
- `propose_assumptions()` returns `Proposal`s scoped by the engine to the claimed
  records, with `loosens=` set whenever a parameter loosens the default

## 7. Status and next steps

Done: extracts and matching; the six checks; certificates and HTML; the MCP server;
hardening from live use and a red-team (scoped and elevated assumptions, file and
SQLite hashing, signed registrations, signed confirmations, SQL filter and
computed-column assumptions, self-consistent certificates, sign-off); `tieout init`;
`tieout demo`; release packaging, CI and publishing.

What comes next, and in what order, is in ROADMAP.md (sections Now, Next, Later and
Under consideration). Keep it up to date as items are finished: move them to Done.

## 8. Working rules

- Tests first; one commit per change, with a clear message.
- Keep the suite passing on Python 3.11 and 3.14, and ruff clean.
- Update SECURITY.md when a change affects what is caught or flagged; its tests check
  it lists every human-only command and red-team attack.
- This repo's `.claude/settings.json` denies the human-only commands and edits inside
  `.tieout/` to Claude Code. Do not work around it.

## 9. Out of scope

LLM calls, dashboards/analytics, writing to any source system, a desktop app UI, any
hosted or paid service, and pre-built system-specific integrations (Cliniko, Xero etc.)
beyond generic SQL/file/Power BI.

## 10. Licence

Apache-2.0 (see LICENSE).
