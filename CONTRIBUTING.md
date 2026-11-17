# Contributing to AI TieOut

Thanks for helping. AI TieOut is a deterministic engine: **the AI proposes, code
proves, people confirm**. Every contribution has to keep that true.

## Getting started

You need [uv](https://docs.astral.sh/uv/). Then:

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

The supported floor is Python 3.11; CI also runs 3.14, on Windows, macOS and
Linux. `uv run tieout demo` runs both fixture pairs end to end.

**Windows path length.** Compiled dependencies (numpy, SQLAlchemy) can fail to load
with "The filename or extension is too long" if the virtualenv sits under a deep
path. Keep the checkout at a short path, or point `UV_PROJECT_ENVIRONMENT` at a
short directory.

### Project layout

- `src/tieout_ai/connectors/`: file (CSV/Excel) and SQL sources; SQL is parsed
  with sqlglot and must be a single read-only `SELECT` that returns every row
- `match.py`: row-level matching and the gap
- `checks/`: the discrepancy checks, one interface and a registry
- `certify.py`: conservation checks, certificates and their verification
- `assumptions.py`, `connections.py`, `session.py`: the on-disk workspace
  (`.tieout/`), read afresh on every call
- `signing.py`: confirmer keys, signed confirmations, registrations and sign-offs
- `service.py` and `mcp_server.py`: what the AI can call (and nothing it must not)
- `cli.py`: the `tieout` command, including the human-only actions
- `render.py`: the HTML certificate
- `fixtures/`: the clean and messy "dashboard vs Xero" pairs; the committed files
  and answer keys are in `fixtures/data/`. Regenerate them with
  `uv run python fixtures/generate.py` and `uv run python fixtures/generate_messy.py`
  (the tests check they are byte-identical).

## Ground rules

- **No LLM calls**, anywhere. Everything must be deterministic and reproducible.
- **Tests first.** Write the failing test, then the code.
- **Conservation.** Buckets plus residual equal the gap exactly; no row is in two
  buckets. The engine enforces this, so a check cannot break it, but tests should
  still show your change keeps it.
- **Honest residuals.** Never make something "explained" that you cannot prove.
- **Loosening is elevated.** Any parameter that loosens a default must produce an
  elevated assumption with a one-line reason.
- **Humans confirm.** Nothing that confirms, signs, registers or refreshes may be
  reachable from the MCP server.

## Write your own check

A check tests one hypothesis about why two sources differ ("these invoices are
dated a month apart", "one side includes GST") and claims only the differences it
can prove. The engine does the rest: it sums the claimed amounts, refuses
overlapping claims, scopes the assumptions to the claimed records, and keeps
conservation.

### How a check works

1. **`Params`**: a pydantic model of the check's parameters. Put the guardrails
   here: hard limits that reject out-of-range values with a clear message.
2. **`prove(ctx, items, params)`**: look at the open discrepancy items and return
   a `Claim` for each one your hypothesis proves. Each item has a `kind`
   (`only_a`, `only_b`, `difference` or `data_quality`), a `key`, its `amount`
   (its contribution to the gap, A minus B), `amount_a` / `amount_b` and its
   `row_ids`. `ctx.value(row_id, column)` reads a cell. If the hypothesis is
   wrong, return nothing: an empty bucket is a valid answer, not an error.
3. **`propose_assumptions(ctx, params, claims)`**: the judgement calls your claims
   rest on, as `Proposal(parameter, value, statement)`. Write `statement` so it
   completes "For these N records (...), "; the engine adds that scope. Name the
   sides with `ctx.name("a")` / `ctx.name("b")`, never "side A". If a parameter
   loosens your check's default, set `loosens="loosens the default: ..."` and the
   assumption becomes elevated.
4. **`name`, `version`, `description`**, then `@register`. Bump `version`
   whenever what the check claims changes; certificates record it.

### Example: a fixed fee

A payment provider deducts a flat $2.50 fee, so the bank shows each payment
$2.50 lower than the dashboard. This check claims exactly those differences:

```python
# Example: the fixed_fee check
from collections.abc import Iterable
from decimal import Decimal

from pydantic import Field, field_validator

from tieout_ai.checks import Check, CheckContext, CheckParams, Claim, Item, Proposal, register
from tieout_ai.checks.base import Side, other

MAX_FEE = Decimal("50")


class FixedFeeParams(CheckParams):
    fee: Decimal = Field(description="The fee deducted from each payment, e.g. 2.50.")
    net_side: Side = Field(description="The side whose amounts are net of the fee.")

    @field_validator("fee")
    @classmethod
    def _within_limits(cls, value: Decimal) -> Decimal:
        # Guardrail: a "fee" this large would explain almost anything.
        if not 0 < value <= MAX_FEE:
            raise ValueError(f"fee must be above $0 and at most ${MAX_FEE}")
        return value


@register
class FixedFeeCheck(Check):
    name = "fixed_fee"
    version = "1.0.0"
    description = "Keys whose amounts differ by exactly a fixed fee deducted on one side."
    Params = FixedFeeParams

    def prove(
        self, ctx: CheckContext, items: list[Item], params: FixedFeeParams
    ) -> Iterable[Claim]:
        # The gap is A minus B, so a fee deducted on side B shows as +fee.
        expected = params.fee if params.net_side == "b" else -params.fee
        return [
            Claim(item) for item in items if item.kind == "difference" and item.amount == expected
        ]

    def propose_assumptions(
        self, ctx: CheckContext, params: FixedFeeParams, claims: list[Claim]
    ) -> list[Proposal]:
        net, gross = ctx.name(params.net_side), ctx.name(other(params.net_side))
        return [
            Proposal(
                "fixed_fee.fee",
                {"fee": params.fee, "net_of_fee": net},
                f"{net} amounts are net of a ${params.fee} fee that {gross} includes.",
            )
        ]
```

Run against a pair where the dashboard shows $102.50 and the bank $100.00, it
claims the $2.50 and records the assumption "For this record (INV-1), bank amounts
are net of a $2.50 fee that dashboard includes." With `fee: 3.00` it claims
nothing; with `fee: 60` it is rejected.

### Where it goes, and how to test it

- Put the check in `src/tieout_ai/checks/your_check.py` and import it in
  `src/tieout_ai/checks/__init__.py` so it registers.
- Test it with small, hand-made extracts (see `tests/support.py`, `make_extract`).
  At minimum show that it claims exactly the items it should, returns an empty
  bucket for a wrong hypothesis, rejects out-of-range parameters, and marks any
  loosened default as elevated.
- If it is a check most people will want, add a case for it to the messy fixture
  (`src/tieout_ai/fixtures/generate_messy.py`) and regenerate the fixtures with
  `uv run python fixtures/generate_messy.py`.

This example is tested: `tests/test_contributing.py` runs the code block above, so
if the interface changes, the guide has to change with it.

## Submitting changes

- One logical change per pull request, with tests.
- Keep CI green: tests on all three operating systems and both Pythons, and ruff.
- Security-relevant changes: say what they let through or stop, and update
  SECURITY.md (its tests check it lists every human-only command and every
  red-team attack). Report vulnerabilities privately, as SECURITY.md describes.

## Releasing

Releases go to PyPI through `.github/workflows/publish.yml`, using PyPI trusted
publishing: no API token is stored anywhere.

One-time setup:

1. On PyPI, add a *trusted publisher* (a "pending publisher" for the first
   release) for the project `tieout-ai`: this GitHub repository, workflow
   `publish.yml`, environment `pypi`.
2. In the GitHub repository settings, create the environment `pypi`. Adding
   yourself as a required reviewer means every upload waits for your approval.

Each release:

1. Set `version` in `pyproject.toml`, commit, and merge to `main`.
2. Create a GitHub release whose tag is `v` plus that version (e.g. `v0.1.0`).
   The workflow refuses a tag that does not match.
3. The workflow builds, checks that the wheel installs and `tieout-ai demo` runs,
   then publishes.
