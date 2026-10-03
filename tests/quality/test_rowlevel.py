"""Failing-row samples (safe by default) and the flag column (W3-06)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.quality import GateSchema, ValidationContext
from shape.quality.gatespec import ColumnSpec, RelationshipSpec, TableSpec
from shape.quality.rowlevel import (
    REDACTED,
    flag_failing_rows,
    is_classified,
    row_outcomes,
    sample_failures,
)

SCHEMA = GateSchema(
    tables={
        "customer": TableSpec(
            "customer",
            {
                "id": ColumnSpec("id", "integer"),
                "email": ColumnSpec("email", "string", nullable=False),
                "score": ColumnSpec("score", "integer", nullable=True),
            },
            primary_key=("id",),
        ),
        "order": TableSpec(
            "order",
            {
                "id": ColumnSpec("id", "integer"),
                "customer_id": ColumnSpec("customer_id", "integer"),
            },
            primary_key=("id",),
        ),
    },
    relationships=(RelationshipSpec("placed_by", "customer", "order", ("id",), ("customer_id",)),),
)

TABLES = {
    "customer": pa.table(
        {
            "id": [1, 2, 2, 4],
            "email": ["a@x.io", None, "c@x.io", "d@x.io"],
            "score": [10, 200, -5, None],
        }
    ),
    "order": pa.table({"id": [1, 2, 3], "customer_id": [1, 9, 2]}),
}


def ctx(config=None):
    return ValidationContext(tables=TABLES, schema=SCHEMA, config=config or {})


def by(outcomes, **kw):
    out = [o for o in outcomes if all(getattr(o, k) == v for k, v in kw.items())]
    assert len(out) == 1, (kw, outcomes)
    return out[0]


def test_null_outcome_marks_rows():
    o = by(row_outcomes("null_constraint", ctx()), table="customer", columns=("email",))
    assert (o.rows, o.failing) == (4, 1)
    assert list(o.failing_rows) == [1]


def test_unique_outcome_marks_repeats_not_first():
    o = by(row_outcomes("unique_constraint", ctx()), table="customer")
    assert o.failing == 1 and list(o.failing_rows) == [2]


def test_referential_outcome_marks_orphans():
    o = by(row_outcomes("referential_integrity", ctx()), table="order")
    assert o.columns == ("customer_id",) and list(o.failing_rows) == [1]


def test_range_outcome_uses_both_bounds():
    c = ctx({"ranges": {"customer.score": {"min": 0, "max": 100}}})
    o = by(row_outcomes("range_constraint", c), table="customer", columns=("score",))
    assert list(o.failing_rows) == [1, 2] and o.rows == 4


def test_temporal_ordering_outcome():
    t = pa.table(
        {
            "s": pa.array([1, 5, None], pa.timestamp("us")),
            "e": pa.array([2, 3, 9], pa.timestamp("us")),
        }
    )
    c = ValidationContext(
        tables={"t": t}, config={"ordering": [{"table": "t", "start": "s", "end": "e"}]}
    )
    o = row_outcomes("temporal_consistency", c)[0]
    assert o.columns == ("s", "e") and list(o.failing_rows) == [1]


def test_gate_without_row_level_support_is_none():
    assert row_outcomes("schema_conformance", ctx()) is None
    assert row_outcomes("distribution", ctx()) is None


def test_clean_data_has_zero_failing():
    t = {"customer": pa.table({"id": [1], "email": ["a"], "score": [1]})}
    c = ValidationContext(tables=t, schema=SCHEMA)
    assert all(o.failing == 0 for o in row_outcomes("null_constraint", c) or [])


def test_samples_redact_classified_columns_by_default():
    outcomes = row_outcomes("null_constraint", ctx())
    # email holds email addresses, so it is classified by its values; nulls stay visible as None
    samples = sample_failures(outcomes, TABLES, limit=3)
    s = samples[0]
    assert s["row"] == 1 and s["table"] == "customer"
    assert s["values"] == {"email": None}


def test_samples_never_show_raw_values_for_classified_columns():
    t = pa.table({"id": [1, 2, 3], "email": ["a@x.io", "b@x.io", "c@x.io"]})
    schema = GateSchema(
        tables={
            "u": TableSpec(
                "u",
                {"id": ColumnSpec("id", "integer"), "email": ColumnSpec("email", "string")},
                primary_key=("email",),
            )
        }
    )
    t = pa.table({"id": [1, 2, 3], "email": ["a@x.io", "a@x.io", "c@x.io"]})
    c = ValidationContext(tables={"u": t}, schema=schema)
    outcomes = row_outcomes("unique_constraint", c)
    assert is_classified("u", "email", t)
    safe = sample_failures(outcomes, {"u": t})
    assert safe[0]["values"] == {"email": REDACTED} and safe[0]["redacted"] is True
    assert "a@x.io" not in repr(safe)
    shown = sample_failures(outcomes, {"u": t}, show_classified=True)
    assert shown[0]["values"] == {"email": "a@x.io"}


def test_declared_classified_columns_are_redacted():
    outcomes = row_outcomes("referential_integrity", ctx())
    plain = sample_failures(outcomes, TABLES)
    assert plain[0]["values"] == {"customer_id": 9}
    declared = sample_failures(outcomes, TABLES, classified={"order": {"customer_id"}})
    assert declared[0]["values"] == {"customer_id": REDACTED}


def test_sample_limit_and_zero():
    outcomes = row_outcomes(
        "range_constraint", ctx({"ranges": {"customer.score": {"min": 0, "max": 100}}})
    )
    assert len(sample_failures(outcomes, TABLES, limit=1)) == 1
    assert sample_failures(outcomes, TABLES, limit=0) == []
    with pytest.raises(ValueError):
        sample_failures(outcomes, TABLES, limit=-1)


def test_sample_is_deterministic_and_lowest_rows_first():
    outcomes = row_outcomes(
        "range_constraint", ctx({"ranges": {"customer.score": {"min": 0, "max": 100}}})
    )
    assert [s["row"] for s in sample_failures(outcomes, TABLES)] == [1, 2]


def test_flag_column_added_and_data_unchanged():
    outcomes = row_outcomes("null_constraint", ctx()) + row_outcomes("unique_constraint", ctx())
    flagged = flag_failing_rows(TABLES["customer"], outcomes, "customer")
    assert flagged.column_names == TABLES["customer"].column_names + ["_shape_dq_failed"]
    assert flagged.column("_shape_dq_failed").to_pylist() == [False, True, True, False]
    assert flagged.select(TABLES["customer"].column_names).equals(TABLES["customer"])


def test_flag_column_custom_name_and_clash():
    flagged = flag_failing_rows(TABLES["order"], [], "order", name="bad")
    assert flagged.column("bad").to_pylist() == [False] * 3
    with pytest.raises(ValueError, match="already"):
        flag_failing_rows(TABLES["order"], [], "order", name="id")


def test_flag_only_counts_outcomes_of_that_table():
    outcomes = row_outcomes("referential_integrity", ctx())
    flagged = flag_failing_rows(TABLES["customer"], outcomes, "customer")
    assert not any(flagged.column("_shape_dq_failed").to_pylist())


def test_failing_rows_dtype():
    o = row_outcomes("null_constraint", ctx())[0]
    assert isinstance(o.failing_rows, np.ndarray)
