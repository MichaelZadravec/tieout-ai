"""Canonical values, canonical JSON and hashing.

Every hash the engine records is computed here, so that the same data always
gives the same hash regardless of which connector produced it or how the
values happened to be typed (Decimal("100.50") and 100.5 hash identically).

Numbers are rendered as plain decimal strings (no exponent, no trailing zeros)
so canonical JSON never contains floats.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np
import pandas as pd


def is_missing(value: Any) -> bool:
    """True for None and every pandas/numpy/float/Decimal spelling of 'no value'."""
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    if isinstance(value, float | np.floating):
        return math.isnan(value)
    if isinstance(value, Decimal):
        return value.is_nan()
    return False


def to_decimal(value: Any) -> Decimal | None:
    """Parse a cell as an exact Decimal. Missing gives None; anything non-numeric raises."""
    if is_missing(value):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is a boolean, not a number")
    if isinstance(value, Decimal | int):
        result = Decimal(value)
    elif isinstance(value, float):
        # repr gives the shortest string that round-trips, so 0.1 -> Decimal("0.1").
        result = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError(f"{value!r} is not a number") from None
    else:
        raise ValueError(f"{value!r} is not a number")
    if not result.is_finite():
        raise ValueError(f"{value!r} is not a finite number")
    return result


def _decimal_str(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def canonical_value(value: Any) -> Any:
    """Map a single cell to a JSON-safe canonical form (str, bool or None)."""
    if is_missing(value):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool | str):
        return value
    if isinstance(value, Decimal | int | float):
        number = to_decimal(value)
        assert number is not None
        return _decimal_str(number)
    if isinstance(value, datetime | date | time):
        # pd.Timestamp is a datetime subclass, so it lands here too.
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"Cannot canonicalise value of type {type(value).__name__}")


def _canonicalise(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {str(k): _canonicalise(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_canonicalise(v) for v in obj]
    return canonical_value(obj)


def canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON: sorted keys, no whitespace, numbers as decimal strings."""
    return json.dumps(
        _canonicalise(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path) -> str:
    """SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_query(query_text: str) -> str:
    """Hash of the exact query text the engine ran."""
    return sha256_hex(query_text.encode("utf-8"))


def hash_frame(frame: pd.DataFrame) -> str:
    """Hash of a table's columns and rows.

    Column order matters; row order does not (rows are sorted by their canonical
    encoding), so a source that returns the same rows in a different order still
    gives the same hash. Duplicate rows are kept.
    """
    rows = [canonical_json(list(row)) for row in frame.itertuples(index=False, name=None)]
    rows.sort()
    digest = hashlib.sha256()
    digest.update(canonical_json([str(c) for c in frame.columns]))
    for row in rows:
        digest.update(b"\n")
        digest.update(row)
    return digest.hexdigest()


def column_totals(frame: pd.DataFrame) -> dict[str, Decimal]:
    """Sum of every column whose non-missing values are all numbers (booleans excluded)."""
    totals: dict[str, Decimal] = {}
    for column in frame.columns:
        total = Decimal(0)
        seen = False
        for value in frame[column]:
            if isinstance(value, bool | np.bool_):
                break
            try:
                number = to_decimal(value)
            except ValueError:
                break
            if number is not None:
                total += number
                seen = True
        else:
            if seen:
                totals[str(column)] = total
    return totals
