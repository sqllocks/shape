"""Placeholder and sentinel detection (#47)."""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape
from shape.profile.joint import detect_placeholders


def _found(values: list, **kw) -> dict[str, dict]:
    col = shape.profile(pa.table({"c": values})).to_dict()["columns"]["c"]
    return {p["value"]: p for p in col.get("placeholders", [])}


@pytest.mark.parametrize(
    ("sentinel", "kind"),
    [
        ("00000", "zeros"),
        ("99999", "numeric_sentinel"),
        ("11111", "repeated_digits"),
        ("1900-01-01", "date_sentinel"),
        ("9999-12-31", "date_sentinel"),
        ("-1", "numeric_sentinel"),
        ("N/A", "text_literal"),
        ("UNKNOWN", "text_literal"),
        ("TEST", "text_literal"),
        ("xxxx", "text_literal"),
        ("aaaa", "repeated_letters"),
    ],
)
def test_listed_sentinels_are_found_with_share_and_evidence(sentinel: str, kind: str) -> None:
    real = [f"v{i:04d}" for i in range(900)]
    found = _found(real + [sentinel] * 100)
    assert sentinel in found, found
    p = found[sentinel]
    assert p["kind"] == kind
    assert p["share"] == pytest.approx(0.1)
    assert p["count"] == 100
    assert sentinel in p["evidence"] and "10.0%" in p["evidence"]


def test_a_spike_in_a_column_of_many_distinct_values_is_found() -> None:
    values = [str(10000 + i) for i in range(940)] + ["12345x"] * 60
    p = _found(values)["12345x"]
    assert p["kind"] == "spike"
    assert p["share"] == pytest.approx(0.06)


def test_a_dominant_value_of_a_low_cardinality_column_is_not_a_placeholder() -> None:
    assert _found(["active"] * 70 + ["closed"] * 20 + ["pending"] * 10) == {}


def test_a_rare_listed_sentinel_is_noise() -> None:
    values = [f"id{i}" for i in range(2000)] + ["N/A"]
    assert _found(values) == {}


def test_a_legitimate_zero_is_not_a_listed_placeholder() -> None:
    assert _found([0, 1, 2, 3, 4] * 200) == {}


def test_nulls_are_not_placeholders_and_shares_are_of_all_rows() -> None:
    values = [None] * 500 + ["-1"] * 100 + [f"v{i}" for i in range(400)]
    p = _found(values)["-1"]
    assert p["share"] == pytest.approx(0.1)  # of all rows
    assert p["share_of_non_null"] == pytest.approx(0.2)


def test_the_detector_works_on_counts_alone() -> None:
    out = detect_placeholders(
        {"0": 0.08, "12345": 0.001}, null_rate=0.0, cardinality=3000, row_count=4000
    )
    assert [p["value"] for p in out] == ["0"]
    assert detect_placeholders(None, null_rate=0.0, cardinality=0, row_count=0) == []


def test_a_profile_without_placeholders_has_no_key() -> None:
    col = shape.profile(pa.table({"c": ["a", "b", "c", "d"] * 50})).to_dict()["columns"]["c"]
    assert "placeholders" not in col
