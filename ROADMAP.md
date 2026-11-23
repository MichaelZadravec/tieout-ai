# Roadmap

AI TieOut is at v0.1. This is what's built, what's coming, and what isn't planned.
The order of **Next** and **Later** will follow the real cases people bring, so if
something here would make it work for you, or something's missing, open an issue or
start a discussion.

The rules every item below must keep are the ones in the README: the AI proposes,
code proves, people confirm. No item adds an LLM to the engine, and nothing a person
must decide can be decided through the AI.

## Done

**v0.1.0**

- Row-level matching of two sources on a key, an amount and a date, for a period.
- Six guarded checks: `timing`, `status_filter`, `tax_basis`, `duplicates`,
  `sign_credit` and `rounding`. Each claims only what it can prove; everything else
  stays unexplained.
- Certificates (JSON and HTML) with buckets, the unexplained residual, every
  assumption and the hashes of the exact data used. Statuses OPEN, PROVISIONAL and
  RECONCILED, and conservation to the cent.
- Assumptions scoped to the rows they cover, with anything that loosens a default
  flagged as elevated.
- Signed source registrations, signed confirmations and signed sign-off, with a
  passphrase-protected key.
- File sources (CSV and Excel) and SQL sources (SELECT only, no truncation, filters
  and computed columns recorded as assumptions).
- The MCP server, `tieout init`, `tieout demo`, CI on three operating systems and
  publishing to PyPI.
- A red-team of the engine, written up in [SECURITY.md](SECURITY.md).

## Now

Making the result something a finance team will trust and use without a terminal.

- **Verdict-first certificate.** The status, the gap and how it's explained at the
  top; hashes and key fingerprints in a verification section at the end.
- **A reason on every elevated confirmation**, signed with the confirmation and
  shown on the certificate.
- **Excel workpaper export.** The certificate as a workbook with live formulas
  (source rows, a matching sheet, one sheet per bucket, a summary where the gap
  minus the buckets equals the residual), so finance can check the workings in
  the tool they already trust.
- **Preparer and reviewer sign-off.** Two separate people and keys; a certificate
  is final only once both have signed.
- **A local review page** for confirming assumptions and signing off, so a reviewer
  never needs a terminal. It keeps the same protections: it runs only on the
  person's machine, needs their passphrase, and is never reachable from the AI.

## Next

Handling the shape real data comes in.

- **ID crosswalks.** Matching when the two systems use different IDs, for example
  an appointment ID and an invoice number. From a reference field, a mapping a
  person registers, or an attribute match the engine scores (coverage, conflicts,
  ambiguous pairs) and a person confirms. Ambiguous pairs are never guessed.
- **Many-to-one matching.** A payment covering several invoices, or an invoice paid
  in instalments. From allocation data first; an exact-sum search only as a
  bounded, elevated fallback, claimed only when exactly one combination fits.
- **Clean-up as assumptions.** Trimming spaces and ignoring case in keys and
  statuses, each recorded as an assumption a person confirms, rather than left to
  the check plan. (The [reasoning test](ai-reasoning-test/) showed why this matters.)
- **Power BI connector.** DAX through the REST `executeQueries` API or XMLA,
  treated as an extract like any other.
- **Tested database guides.** Live tests and setup notes for PostgreSQL and
  SQL Server, including which driver to install.

## Later

Month-end differences that live in the ledger.

- **GL account mode.** Tie an operational system to the general ledger by account
  first, through a chart-of-accounts mapping, then drill into the accounts that
  don't tie.
- **New checks:** manual journals, accruals that reverse next period, deferred
  revenue (the gap equals the movement in the deferred revenue account), grain
  mismatch (line vs header), coding and mapping differences, FX, scope (site,
  entity, intercompany), and timezone cut-off (UTC vs local).

## Under consideration

- **Anchoring certificates outside the user's machine**, so a reader can check a
  certificate and the signer's key fingerprint against something the AI cannot
  write. See SECURITY.md, "Known limitations".
- **Packaging for Claude as a plugin**, bundling the MCP server with a skill that
  guides an investigation.

## Out of scope

These are deliberate, not gaps:

- LLM calls inside the engine.
- Writing to any source system.
- Dashboards and analytics.
- A desktop app.
- A hosted or paid service.
- Pre-built integrations for specific products (Xero, Cliniko and so on). The engine
  stays generic: files, SQL and Power BI.
