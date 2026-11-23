# AI TieOut

**Prove that two business numbers agree, or explain every dollar of the difference.**

AI is already good at building dashboards, but that was never where most of the work is. The next frontier is AI building semantic models and reporting that spans platforms. It has some hurdles to clear first, and the first one is trust in the numbers. That's what AI TieOut is for: your AI assistant investigates, the engine proves, and you confirm. At the end you get a documented, checkable record of how one number ties to the other, for whoever has to sign it off.

## The problem

If an AI tells you it did something, did it actually do it? And what did it assume to get there?

In finance and reconciliations, those assumptions can be big ones. In [our own test](ai-reasoning-test/), an AI found every real cause of a gap, then quietly decided the parts the data couldn't settle and told us the numbers fully reconciled. It sounded most confident exactly where it should have been least sure.

Every month-end asks the same question: *why doesn't the dashboard match the
ledger?* Or the CRM, the ERP, or that random SSRS report.

The answer is usually mundane. Invoices dated by appointment on one side and by
issue date on the other. Voided invoices one system still counts. GST included here
and excluded there. A batch exported twice. Finding it takes hours of filtering
spreadsheets, and the explanation ends up in a workbook nobody can audit or, even
worse, in one person's head, which builds key-person risk.

AI assistants are now good at the investigating part. They can read the data, spot
patterns and form hypotheses in minutes. But an answer that *sounds* right is not
an answer that *is* right. In finance and board reporting, that difference matters.
And anyone who works with large datasets will tell you: once people start doubting
what they're reading, it's hard to win their confidence back.

## AI proposes, code proves, people confirm

AI TieOut splits the job so each part is done by whoever can be trusted with it.

- **The AI proposes.** It explores the sources, writes queries, chooses how to match
  them and suggests why they differ ("these 58 invoices are dated a month apart").
  It works through an MCP server and passes only references and parameters, never
  numbers it claims are results.
- **Code proves.** The engine fetches the data itself, matches it row by row, tests
  each hypothesis with a guarded check, and sums every amount. It claims only the
  differences it can prove. Everything else stays *unexplained*, never rounded
  away. Buckets plus residual always equal the gap, to the cent.
- **People confirm.** Every judgement call (a filter, a tax rate, a date basis) is
  recorded as an assumption, scoped to the records it covers. Anything that loosens
  a default is flagged *elevated*. Only a person, in their own terminal and with
  their own signing key, can confirm an assumption or sign a certificate off.

There are no LLM calls in the engine. Everything is deterministic and reproducible.

## Try it in 60 seconds

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Then:

```bash
uvx tieout-ai demo
```

This runs two "dashboard vs Xero" reconciliations end to end in a temporary folder
and opens their certificates in your browser:

- **Clean**: a $29,072 gap, fully explained by timing, voided invoices, GST,
  duplicates and credit notes. **RECONCILED** and signed off.
- **Messy**: real-world dirt (case and whitespace variants, blank amounts,
  near-duplicates, cent rounding). Most of the gap is explained; the rest
  ($1,326.00 net) is honestly reported as **unexplained**, item by item.

The demo confirms and signs with a throwaway key it makes for the purpose, so
nothing touches your machine's real setup.

## How it works

1. **A person registers the sources**: CSV or Excel files, or SQL databases
   (read-only, `SELECT` only). Each registration is hashed and signed with the
   person's key, so the engine notices if a file changes or a source is swapped.
2. **The AI investigates** through the MCP server: it profiles the sources, runs
   queries, matches the two sides on keys, amounts and dates for a period, and
   reads the differences that are left.
3. **The AI tries hypotheses**, and the engine tests them with built-in checks.
   Each check has hard guardrails and claims only what it can prove:

   | Check | Explains |
   |---|---|
   | `timing` | The same transaction dated in different periods on each side |
   | `status_filter` | Rows of a status (e.g. voided) one side leaves out |
   | `tax_basis` | One side including tax (e.g. 10% GST) and the other not |
   | `duplicates` | Rows one side repeats |
   | `sign_credit` | Credit notes with opposite signs, or missing on one side |
   | `rounding` | Differences of a cent or so, within a strict tolerance |

4. **The engine issues a certificate**: every bucket of explained difference with
   its rows and amounts, the unexplained residual, every assumption, and the hashes
   of the exact data used. Its status is **OPEN** (something is unexplained),
   **PROVISIONAL** (all explained, but resting on unconfirmed assumptions) or
   **RECONCILED** (all explained, all confirmed).
5. **A person confirms and signs off.** `tieout confirm` and `tieout cert sign` run
   only in an interactive terminal and need the person's passphrase. A RECONCILED
   certificate is final only once it is signed off.

### On your own data

```bash
uv tool install tieout-ai
```

```bash
tieout init
```

`tieout init` sets up the current folder: the workspace, the MCP server for Claude
Code, permission rules that keep the human-only commands away from the AI, and
your signing key. Then register your sources:

```bash
tieout connections add-file dashboard path/to/dashboard.csv
```

```bash
tieout connections add-sql ledger
```

A SQL database needs its driver installed alongside the tool, for PostgreSQL for
example `uv tool install tieout-ai --with "psycopg[binary]"`, and a URL such as
`postgresql+psycopg://readonly_user@host/database`.

Open Claude Code in that folder and ask it to reconcile, in plain English. Other
MCP clients can run the server directly with `tieout-mcp --home .tieout`. When the
AI reports assumptions to confirm, review them and confirm the ones you agree with:

```bash
tieout assumptions list
```

```bash
tieout confirm <assumption_id>
```

When it reports a RECONCILED certificate, check it and sign it off:

```bash
tieout cert show <certificate_id> --html certificate.html
```

```bash
tieout cert sign <certificate_id>
```

## Limitations

AI TieOut is a v0.1 proof of concept. It shows where AI-assisted reconciliation
could go; it isn't a finished product. Its limits:

- **Sources:** CSV and Excel files, SQLite, and SQL databases through SQLAlchemy.
  The test suite covers SQLite, and the engine has been run against a live
  PostgreSQL 16 server with a read-only user. SQL Server and MySQL should work
  the same way but haven't been tested yet. There's no Power BI connector yet.
- **Matching needs a shared key.** Both sources must carry the same ID (an invoice
  number, say). When systems use different IDs, you need a mapping first.
- **One row matches one row.** A payment that covers several invoices isn't matched
  yet.
- **Six checks.** Differences that live only in the ledger, such as accruals, manual
  journals and deferred revenue, aren't explained yet. They stay unexplained, which
  is honest but not yet helpful.
- **It explains the difference; it doesn't say which source is right.** If both
  systems hold the same wrong number, they reconcile. If they differ, the
  certificate shows exactly where and why, not which one to believe.
- **Confirming and signing off happen in a terminal.** A local review page is on
  the way.
- **Tested on synthetic data.** The demo and the reasoning test use generated data.
  Real-world cases are the most useful contribution you could make.

The [roadmap](ROADMAP.md) says what's coming next and what's deliberately out of
scope.

## Your data and the AI

The engine runs on your machine and sends nothing anywhere. But the AI assistant
that drives it does see your data, and so does the company that provides that AI.
Through the MCP tools, the AI receives:

- a profile of each source: column names, counts, minimums, maximums and totals,
  and up to 20 example values per column;
- any rows it asks for, a page at a time;
- every unexplained item, with its key and both amounts.

If the AI also has a shell, as it does in Claude Code, it can read your files
directly too. So treat using AI TieOut as sending finance data to your AI provider:
use an AI covered by your company's agreement and data policy, and use exports that
leave out anything the reconciliation doesn't need, such as customer names. A
privacy mode, where the AI works from keys and totals only, is on the
[roadmap](ROADMAP.md).

## Security

The AI is not trusted, and AI TieOut assumes it may have a shell and write access
to the same files you do. So the important protections are cryptographic and
detective, not permissions:

- Sources are hashed and their registrations signed, so an edited file or a
  swapped source keeps the certificate OPEN, or is refused outright.
- Confirmations and sign-offs are signed with the person's passphrase-protected
  key. A confirmation typed into a file, or signed with any other key, does not
  count.
- SQL may not truncate results. Filters and computed columns become assumptions,
  and anything that loosens a default is flagged elevated.
- Certificates are hashed and must be self-consistent, so altered or forged ones
  are refused or flagged as unverifiable.
- Every certificate shows the fingerprint of the key behind it. Check it against
  `tieout keys fingerprint` on the signer's machine, or against the fingerprint the
  signer publishes.

[SECURITY.md](SECURITY.md) has the threat model, what is caught, flagged and out of
scope, and the results of a red-team exercise, including its known limitations.

## Why I built this

I'm an analytics engineer with a finance background. A lot of my working life has
gone into the question every month-end asks: why doesn't this number match that
one? The answer is almost always findable, but it takes hours, and the result
usually lives in a spreadsheet that only its author can follow.

AI assistants changed the first half of that job overnight. They are fast,
tireless investigators. But I wouldn't sign off on a number because an AI said it
was fine, and I wouldn't ask a CFO to either. What I wanted was the speed of the AI
with the rigour of a reconciliation that proves itself: every dollar traced to
rows, every judgement call written down and confirmed by a person.

AI TieOut is that tool. I hope it saves you some month-ends.

## Test an AI yourself

[ai-reasoning-test/](ai-reasoning-test/) is a reproducible test of how an AI chat
assistant reasons about a reconciliation when some of the gap genuinely can't be
determined from the data. It has the files, the exact prompts (and why each is
worded the way it is), answer keys, a scoring sheet and my raw results, so you can
run it on your own computer and check my conclusions.

## Contributing

Contributions are welcome, especially new checks. [CONTRIBUTING.md](CONTRIBUTING.md)
explains how to set up, the ground rules, and how to write your own check.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
