from decimal import Decimal
from pathlib import Path

from recon_engine.fixtures.generate import DASHBOARD_FILE, MANIFEST_FILE, XERO_FILE, generate

COMMITTED = Path(__file__).parent.parent / "fixtures" / "data"


def test_generator_is_deterministic_and_matches_the_committed_files(tmp_path):
    generate(tmp_path)
    for name in (DASHBOARD_FILE, XERO_FILE, MANIFEST_FILE):
        assert (tmp_path / name).read_bytes() == (COMMITTED / name).read_bytes(), name


def test_injected_issues_match_the_brief(tmp_path):
    injected = generate(tmp_path)["injected"]

    assert injected["timing"]["count"] == 58
    assert Decimal(injected["timing"]["amount"]) == Decimal(26900)
    assert injected["status_filter"]["count"] == 3
    assert Decimal(injected["status_filter"]["amount"]) == Decimal(600)
    for name in ("tax_basis", "duplicates", "sign_flip", "missing_credit_notes"):
        assert injected[name]["count"] > 0


def test_injected_keys_do_not_overlap(tmp_path):
    injected = generate(tmp_path)["injected"]
    keys = [key for entry in injected.values() for key in entry["keys"]]

    assert len(keys) == len(set(keys))


def test_expected_gap_is_the_sum_of_the_injected_amounts(tmp_path):
    summary = generate(tmp_path)
    total = sum(Decimal(entry["amount"]) for entry in summary["injected"].values())

    assert Decimal(summary["expected_gap"]) == total
