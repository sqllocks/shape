"""Regression tests for gate defects found by the AUD-quality audit."""

from __future__ import annotations

from datetime import datetime

import pyarrow as pa
import pytest

from shape.quality import (
    ColumnSpec,
    DistributionGate,
    GateSchema,
    RangeConstraintGate,
    ReferentialIntegrityGate,
    RelationshipSpec,
    TableSpec,
    TemporalConsistencyGate,
    ValidationContext,
)


def composite(parent_columns=("a", "b"), child_columns=("a", "b")) -> GateSchema:
    return GateSchema({}, (RelationshipSpec("r", "p", "c", parent_columns, child_columns),))


PARENT = pa.table({"a": [1, 2], "b": [10, 20]})


# #466: a composite foreign key was checked column by column
def test_a_composite_foreign_key_is_checked_as_a_whole():
    child = pa.table({"a": [1, 2, 1], "b": [10, 20, 20]})  # (1, 20) has no parent
    r = ReferentialIntegrityGate().check(
        ValidationContext(tables={"p": PARENT, "c": child}, schema=composite())
    )
    assert not r.passed
    assert list(r.details["orphan_counts"].values()) == [1]
    assert "1 orphan" in r.errors[0]


def test_a_composite_key_with_a_null_part_is_not_an_orphan():
    child = pa.table({"a": [1, None, 2], "b": [10, 99, None]})
    r = ReferentialIntegrityGate().check(
        ValidationContext(tables={"p": PARENT, "c": child}, schema=composite())
    )
    assert r.passed, r.errors


def test_a_composite_key_of_text_and_numbers_compares_by_value():
    parent = pa.table({"a": pa.array([1, 2], pa.int32()), "b": ["x", "y"]})
    child = pa.table({"a": pa.array([1, 2], pa.int64()), "b": ["x", "x"]})
    r = ReferentialIntegrityGate().check(
        ValidationContext(tables={"p": parent, "c": child}, schema=composite())
    )
    assert list(r.details["orphan_counts"].values()) == [1]


def test_parent_and_child_columns_of_different_lengths_are_an_error():
    child = pa.table({"a": [1], "b": [20]})
    r = ReferentialIntegrityGate().check(
        ValidationContext(tables={"p": PARENT, "c": child}, schema=composite(("a", "b"), ("a",)))
    )
    assert not r.passed and "2 parent columns and 1 child column" in r.errors[0]


TS = pa.table({"ts": pa.array([datetime(2099, 1, 1)], pa.timestamp("us"))})


# #467: a no_future entry for a missing table or column was skipped silently
@pytest.mark.parametrize("spec", ["t.tss", "tx.ts", "nodot"])
def test_a_no_future_entry_that_matches_nothing_is_a_warning(spec):
    r = TemporalConsistencyGate().check(
        ValidationContext(tables={"t": TS}, config={"no_future": [spec]})
    )
    assert any(spec in w for w in r.warnings), r.warnings


def enum_ctx(values: list[object], enum: dict[str, float], typ: str) -> ValidationContext:
    cols = {"g": ColumnSpec("g", typ, enum=enum)}
    return ValidationContext(
        tables={"t": pa.table({"g": values})}, schema=GateSchema({"t": TableSpec("t", cols)})
    )


# #468: an enum on an integer or boolean column never matched (JSON keys are text)
@pytest.mark.parametrize(
    ("values", "enum", "typ"),
    [
        ([1, 2] * 30, {"1": 0.5, "2": 0.5}, "integer"),
        ([True, False] * 30, {"true": 0.5, "false": 0.5}, "boolean"),
        ([True, False] * 30, {"True": 0.5, "False": 0.5}, "boolean"),
    ],
)
def test_an_enum_on_a_non_text_column_is_tested(values, enum, typ):
    r = DistributionGate().check(enum_ctx(values, enum, typ))
    assert not r.warnings, r.warnings
    assert r.details["t.g"]["p_value"] > 0.9


# #472: a range on a column without numbers checked nothing and said nothing
@pytest.mark.parametrize(
    "table", [pa.table({"x": ["a", "b"]}), TS.rename_columns(["x"]), pa.table({"x": [None, None]})]
)
def test_a_range_on_a_column_without_numbers_is_a_warning(table):
    r = RangeConstraintGate().check(
        ValidationContext(tables={"t": table}, config={"ranges": {"t.x": {"min": 0}}})
    )
    assert r.passed and any("t.x" in w for w in r.warnings), r.warnings
