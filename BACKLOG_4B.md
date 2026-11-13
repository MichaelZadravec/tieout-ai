# Phase 4b — Hardening from live testing

> Brief for Claude Code. Save in the repo root next to CLAUDE.md.
> These items come from the first live tests in a customer-style folder (C:\dev\recon_trial).
> The goal: the engine, not the AI's good behaviour, must stop a bad certificate.

## Working rules

- Tests first for every item. Commit after each item with a clear message.
- Keep all existing tests passing on Python 3.11 and 3.14. Lint clean.
- Update fixtures, manifests and the README as behaviour changes.
- **Stop for review after Part A, and again after Part B.**
- At the end, reinstall the trial tool: `uv tool install --reinstall --force --python 3.14 C:\dev\recon_engine`

---

## Part A — Correctness and guardrails

### A1. Bug: the server caches assumptions (do this first)
**Found:** confirmations made with `recon confirm` while the MCP server was running were ignored by `certify`. The server loaded the assumption store once at startup.
**Fix:** every read of assumptions (certify, get_certificate, listing) reloads from disk. Treat the on-disk store as the only source of truth. Check whether any other store (connections, certificates) has the same caching problem and fix it the same way.
**Done when:** a test starts the server, confirms an assumption through the CLI path, then certifies, and gets RECONCILED without a restart.

### A2. Assumption wording: real names, scoped to what was proven
**Found:** assumptions say "Side A / Side B", and some read broader than the evidence (e.g. "Side B amounts include tax at 10%" when only 12 invoices were proven).
**Fix:**
- Use connection names (e.g. "dashboard", "xero") instead of Side A/B everywhere: assumptions, certificate, HTML, CLI.
- Scope each assumption to the rows it covers: row count plus the key list (or first/last keys plus count when long), e.g. "For these 12 invoices (INV-10402 … INV-10413), xero amounts include 10% GST and dashboard amounts exclude it."
- Assumption IDs stay deterministic. Include the scope in the ID hash so a different set of rows gives a different assumption.
**Done when:** no "Side A/B" text remains in user-facing output, and the clean fixture's assumptions each name their rows.

### A3. Guardrail: `compare_columns` can't drop core columns
**Found:** in the messy test the AI excluded the date column from `compare_columns`, so INV-20191 (same invoice on two different dates) was claimed as a duplicate, which the fixture defines as NOT a duplicate.
**Fix:** `compare_columns` must always include the match key, the amount column and every date column in the match spec. Reject the call with a clear error otherwise. Bump the duplicates check version.
**Done when:** the INV-20191 case can no longer be claimed by duplicates, and a test shows the rejection message.

### A4. "Elevated" assumptions
**Fix:** any parameter that loosens a check's default produces an assumption marked `elevated: true`. At minimum:
- duplicates with `compare_columns` narrower than all columns
- status filter with more than one value variant (e.g. VOIDED / Voided / " VOIDED")
- any tolerance above zero (A5)
- a timing window wider than the check's default
- query filters beyond the period (B2)

Elevated assumptions are shown separately at the top of the HTML certificate and in `recon assumptions list`, with a one-line reason ("loosens the default: ignores columns load_id, loaded_at").
**Done when:** the messy fixture's status-variant and compare_columns assumptions show as elevated in the HTML and CLI.

### A5. New check: rounding tolerance
**Found:** INV-20185, INV-20186 (10% GST plus 1 cent rounding) and INV-20187 (1 cent) stay unexplained because checks need exact matches.
**Fix:**
- New check `rounding`: claims amount differences on matched keys where |difference| ≤ tolerance. Default tolerance $0.01 per row, hard maximum $0.05. Tolerance is logged as an elevated assumption.
- Add an optional `rounding_tolerance` to `tax_basis` (default 0, max $0.01) so tax-plus-a-cent cases can be claimed, logged as elevated.
- No row may be claimed by both checks (existing conservation rule).
**Done when:** on the messy fixture the three rounding items are claimed and the residual drops by exactly $9.78; a tolerance over the maximum is rejected.

**→ Stop for review after Part A.**

---

## Part B — Closing the tampering gaps

### B1. File sources: hash at registration
**Found:** the AI could edit a CSV before the engine reads it, and the extract hash would just fingerprint the edited file.
**Fix:**
- `recon connections add-file` records the file's hash, size and modified time at registration.
- Every extract re-checks. If the file has changed, the extract is marked `changed_since_registration` and the certificate can't be better than OPEN. Show a clear warning naming the file.
- New command `recon connections refresh <id>`: re-registers the file. It is interactive-only (TTY plus retyped code, same as confirm), and records who refreshed it and when.
**Done when:** a test edits a registered CSV and the certificate is OPEN with the warning; after a refresh it can be RECONCILED again.

### B2. Query filters become assumptions
**Found:** the AI can exclude awkward rows with a WHERE clause; the query is recorded but nobody is alerted.
**Fix:** parse every SQL extract with sqlglot.
- Reject outright: LIMIT / TOP / FETCH / SAMPLE / TABLESAMPLE (these truncate data).
- Every WHERE / HAVING / JOIN condition / DISTINCT becomes an auto-generated assumption that needs human confirmation, worded in plain English where possible plus the raw SQL fragment.
- A filter that only restricts to the reconciliation period is a normal assumption. Any other filter is elevated.
**Done when:** a test query with `WHERE status <> 'PAID'` produces an elevated unconfirmed assumption and the certificate can't be RECONCILED until it's confirmed; a query with LIMIT is rejected.

### B3. Signed confirmations
**Found:** the deny rules only stop Claude Code's tools. A shell command could write `"confirmed": true` straight into `.recon/assumptions.json`.
**Fix:**
- `recon keys init` (interactive only): creates an Ed25519 keypair. The private key is encrypted with a passphrase the person sets and stored outside the workspace (user profile, e.g. `%APPDATA%\recon\keys\`). Use the `cryptography` package.
- `recon confirm` asks for the passphrase and signs: assumption ID + assumption content hash + confirmer name + timestamp.
- `certify` verifies every confirmation against the public key. A missing, invalid or mismatched signature counts as unconfirmed, and the certificate lists it as "confirmation could not be verified".
- The certificate records the public key fingerprint and the confirmer's name.
- Editing an assumption's text after confirmation invalidates the signature (content hash).
**Done when:** a test that hand-edits `"confirmed": true` into the store is treated as unconfirmed; a properly signed confirm still gives RECONCILED.

### B4. `recon init`
**Found:** customer setup took ~8 manual steps and broke on PATH twice.
**Fix:** `recon init` in an empty folder, idempotent:
- creates the `.recon/` workspace
- writes `.mcp.json` (`"command": "recon-mcp"`, `--home .recon`)
- writes `.claude/settings.json` with:
  - **allow:** `mcp__recon__*`
  - **deny:** `recon confirm`, `recon keys`, `recon connections` (all subcommands, since refresh/add must be human) in both Bash and PowerShell; Edit on `.recon/**`
- checks whether `recon` / `recon-mcp` are on PATH; if not, prints the exact fix (`uv tool update-shell`, then open a new terminal)
- runs `recon keys init` if no key exists yet
- prints a short "next steps" list (add connections, open Claude Code here, confirm in your own terminal)
**Done when:** running `recon init` in an empty folder produces a working trial folder equivalent to C:\dev\recon_trial, and running it twice changes nothing.

**→ Stop for review after Part B.**

---

## Update CLAUDE.md principles
Add to section 2:
- The on-disk stores are the only source of truth; never cache them across calls.
- Any parameter or filter that loosens a default must produce an elevated assumption.
- File sources must match their registered hash; SQL must not truncate.
- Confirmations are valid only if signed with the confirmer's key.
- Anything that registers, refreshes or confirms is human-only and interactive.
