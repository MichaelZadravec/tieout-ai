"""The "write your own check" example in CONTRIBUTING.md must actually work."""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from support import make_extract

from tieout_ai.checks import CheckContext, CheckParameterError, get_check
from tieout_ai.checks import base as checks_base
from tieout_ai.match import match
from tieout_ai.models import MatchSpec

DOC = Path(__file__).parent.parent / "CONTRIBUTING.md"
MARKER = "# Example: the fixed_fee check"


@pytest.fixture
def fixed_fee():
    """Run the guide's code block, which registers the check; unregister it afterwards."""
    blocks = re.findall(r"```python\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    [example] = [b for b in blocks if b.startswith(MARKER)]
    exec(compile(example, "CONTRIBUTING.md", "exec"), {"__name__": "contributing_example"})
    try:
        yield get_check("fixed_fee")
    finally:
        checks_base._REGISTRY.pop("fixed_fee", None)


def pair(amount_a, amount_b):
    a = make_extract([("INV-1", "2026-10-03", amount_a, "PAID")], "dashboard")
    b = make_extract([("INV-1", "2026-10-03", amount_b, "PAID")], "bank")
    spec = MatchSpec(
        extract_a=a.extract.id,
        extract_b=b.extract.id,
        key_columns=["invoice_id"],
        amount_column="amount",
    )
    return CheckContext(a=a, b=b, spec=spec, result=match(a, b, spec))


def test_the_example_claims_what_it_can_prove(fixed_fee):
    ctx = pair("102.50", "100.00")
    bucket = fixed_fee.run(ctx, {"fee": "2.50", "net_side": "b"})
    [assumption] = ctx.assumptions.get_many(bucket.assumption_ids)

    assert bucket.amount == Decimal("2.50")
    assert assumption.description == (
        "For this record (INV-1), bank amounts are net of a $2.50 fee that dashboard includes."
    )


def test_the_example_returns_an_empty_bucket_when_wrong(fixed_fee):
    assert fixed_fee.run(pair("102.50", "100.00"), {"fee": "3.00", "net_side": "b"}).item_ids == []
    assert fixed_fee.run(pair("102.50", "100.00"), {"fee": "2.50", "net_side": "a"}).item_ids == []


def test_the_example_enforces_its_guardrail(fixed_fee):
    with pytest.raises(CheckParameterError, match="fee must be above"):
        fixed_fee.run(pair("150.00", "100.00"), {"fee": "50.01", "net_side": "b"})


def test_the_guide_covers_the_essentials():
    doc = DOC.read_text(encoding="utf-8")
    for heading in (
        "## Getting started",
        "## Write your own check",
        "## Releasing",
    ):
        assert heading in doc, heading
    for must in ("prove", "propose_assumptions", "loosens", "@register", "version", "SECURITY.md"):
        assert must in doc, must
