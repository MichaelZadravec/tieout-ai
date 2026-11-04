from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from recon_engine.hashing import (
    canonical_json,
    canonical_value,
    column_totals,
    hash_frame,
    hash_query,
)


def frame(rows, columns=("id", "amount")):
    return pd.DataFrame([list(r) for r in rows], columns=list(columns), dtype=object)


def test_canonical_json_is_independent_of_key_order():
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})


def test_canonical_json_is_compact_sorted_and_numbers_are_strings():
    assert canonical_json({"b": 1, "a": "x", "c": None}) == b'{"a":"x","b":"1","c":null}'


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (Decimal("100.50"), 100.5),
        (Decimal("100"), 100),
        (Decimal("1E+2"), 100),
        (0.1, Decimal("0.1")),
        (-0.0, 0),
    ],
)
def test_equal_numbers_canonicalise_identically(left, right):
    assert canonical_value(left) == canonical_value(right)


def test_numbers_render_without_exponent():
    assert canonical_value(Decimal("1E+3")) == "1000"
    assert canonical_value(Decimal("1E-7")) == "0.0000001"


@pytest.mark.parametrize("missing", [None, float("nan"), pd.NA, pd.NaT, Decimal("NaN")])
def test_missing_values_canonicalise_to_none(missing):
    assert canonical_value(missing) is None


def test_dates_canonicalise_to_iso():
    assert canonical_value(date(2026, 10, 3)) == "2026-10-03"
    assert canonical_value(datetime(2026, 10, 3, 9, 30)) == "2026-10-03T09:30:00"
    assert canonical_value(pd.Timestamp("2026-10-03 09:30")) == "2026-10-03T09:30:00"


def test_same_data_always_gives_same_hash():
    rows = [("A", "10.50"), ("B", "2")]
    first = hash_frame(frame(rows))
    assert first == hash_frame(frame(rows))
    assert first == hash_frame(frame(list(rows)))
    assert len(first) == 64


def test_hash_ignores_row_order():
    assert hash_frame(frame([("A", "1"), ("B", "2")])) == hash_frame(
        frame([("B", "2"), ("A", "1")])
    )


def test_hash_changes_when_a_value_changes():
    assert hash_frame(frame([("A", "1")])) != hash_frame(frame([("A", "1.01")]))


def test_hash_changes_when_columns_are_renamed():
    assert hash_frame(frame([("A", "1")])) != hash_frame(frame([("A", "1")], columns=("id", "amt")))


def test_hash_distinguishes_duplicate_rows():
    assert hash_frame(frame([("A", "1")])) != hash_frame(frame([("A", "1"), ("A", "1")]))


def test_query_hash_is_stable_and_sensitive():
    assert hash_query("SELECT 1") == hash_query("SELECT 1")
    assert hash_query("SELECT 1") != hash_query("SELECT 2")
    assert len(hash_query("SELECT 1")) == 64


def test_column_totals_cover_numeric_columns_only():
    data = frame(
        [("A", "10.50", "x", True), ("B", 2, "y", False), ("C", None, "z", True)],
        columns=("id", "amount", "note", "flag"),
    )
    assert column_totals(data) == {"amount": Decimal("12.50")}
