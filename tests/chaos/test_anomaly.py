"""The anomaly entry point: rate, kinds, schema and row count, protection, determinism."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.chaos import KINDS, inject_anomalies
from shape.plugins.api.v1 import ChaosReport


def _batch(n: int = 2000) -> pa.RecordBatch:
    rng = np.random.default_rng(5)
    return pa.RecordBatch.from_pydict(
        {
            "id": pa.array(range(n), type=pa.int64()),
            "qty": pa.array(rng.integers(1, 50, n), type=pa.int32()),
            "amount": pa.array(rng.random(n) * 100 + 1),
            "name": pa.array([f"name{i}" for i in range(n)]),
            "ts": pa.array(
                np.datetime64("2024-01-01T00:00:00") + np.arange(n).astype("timedelta64[s]"),
                type=pa.timestamp("us"),
            ),
            "day": pa.array(np.datetime64("2024-01-01") + np.arange(n) % 300, type=pa.date32()),
        }
    )


def test_changes_the_requested_fraction_and_keeps_schema_and_rows() -> None:
    b = _batch()
    r = inject_anomalies(b, fraction=0.05, seed=1, protect=["id"])
    assert r.batch.schema == b.schema and r.batch.num_rows == b.num_rows
    assert len(r.rows) == 100 == r.report.rows_affected
    assert list(r.rows) == sorted(set(r.rows))
    assert isinstance(r.report, ChaosReport)
    assert set(r.kinds) <= set(KINDS)
    assert r.batch.column("id").equals(b.column("id"))


def test_every_reported_row_differs_and_no_other_row_does() -> None:
    b = _batch()
    r = inject_anomalies(b, fraction=0.1, seed=2)
    changed = set()
    for name in b.schema.names:
        old, new = b.column(name), r.batch.column(name)
        for i in range(b.num_rows):
            if old[i].as_py() != new[i].as_py():
                changed.add(i)
    assert changed == set(r.rows)


def test_each_kind_does_what_it_says() -> None:
    b = _batch()
    for kind in KINDS:
        r = inject_anomalies(b, fraction=0.2, seed=3, kinds=[kind], protect=["id"])
        assert set(r.kinds) == {kind} and len(r.rows) > 0
        for row, col in zip(r.rows, r.columns, strict=True):
            old, new = b.column(col)[row].as_py(), r.batch.column(col)[row].as_py()
            if kind == "null":
                assert new is None and old is not None
            elif kind == "out_of_range":
                assert abs(new) >= 100 * abs(old) or abs(new) >= 100
            elif kind == "negative":
                assert new == -old
            elif kind == "future_date":
                assert (new.year if hasattr(new, "year") else 0) >= 2031
            else:
                assert new.startswith("﻿") or new[-1] in "\xe9\xf1\xfc\xe4\xf6"


def test_fraction_is_exact_in_expectation_for_small_batches() -> None:
    b = _batch(10)
    total = sum(
        len(inject_anomalies(b, fraction=0.05, seed=s, kinds=["null"], protect=["id"]).rows)
        for s in range(4000)
    )
    assert abs(total / 4000 - 0.5) < 0.05  # 10 rows x 5% = 0.5 expected per batch


def test_deterministic_and_does_not_modify_the_input() -> None:
    b = _batch()
    before = pa.Table.from_batches([b])
    a = inject_anomalies(b, fraction=0.1, seed=9)
    c = inject_anomalies(b, fraction=0.1, seed=9)
    assert a.batch.equals(c.batch) and a.rows == c.rows and a.report == c.report
    assert pa.Table.from_batches([b]).equals(before)
    assert not a.batch.equals(inject_anomalies(b, fraction=0.1, seed=10).batch)


def test_edges() -> None:
    b = _batch(50)
    assert inject_anomalies(b, fraction=0.0, seed=1).batch.equals(b)
    full = inject_anomalies(b, fraction=1.0, seed=1, kinds=["null"], protect=["id"])
    assert len(full.rows) == 50
    empty = pa.RecordBatch.from_pydict({"a": pa.array([], type=pa.int64())})
    assert inject_anomalies(empty, fraction=0.5, seed=1).rows == ()
    only_protected = inject_anomalies(b, fraction=0.5, seed=1, protect=b.schema.names)
    assert only_protected.rows == ()
    with pytest.raises(ValueError):
        inject_anomalies(b, fraction=1.5, seed=1)
    with pytest.raises(ValueError):
        inject_anomalies(b, fraction=0.1, seed=1, kinds=["nope"])


def test_integer_out_of_range_stays_in_type_range() -> None:
    b = pa.RecordBatch.from_pydict({"x": pa.array([2**31 - 5] * 100, type=pa.int32())})
    r = inject_anomalies(b, fraction=1.0, seed=1, kinds=["out_of_range"])
    assert r.batch.schema == b.schema and len(r.rows) == 100


def test_non_nullable_fields_are_never_nulled() -> None:
    schema = pa.schema([pa.field("k", pa.int64(), nullable=False)])
    b = pa.RecordBatch.from_arrays([pa.array(range(20))], schema=schema)
    assert inject_anomalies(b, fraction=1.0, seed=1, kinds=["null"]).rows == ()
