"""AUD-chaos regressions for the targeted corruptions and the ground-truth log."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from shape.chaos.groundtruth import Corruption, corrupt_tables


def test_orphan_keys_match_no_parent_row_without_declared_references() -> None:
    """#397: with no ``references`` the parent is found by the column name, so no orphan key
    is a real parent key even when the parent's keys go far above the child's."""
    parent = pa.table({"customer_id": pa.array(np.arange(1, 20_000_001, 100), pa.int64())})
    child = pa.table(
        {
            "order_id": pa.array(np.arange(1000), pa.int64()),
            "customer_id": pa.array(np.arange(1000) * 100 + 1, pa.int64()),
        }
    )
    out = corrupt_tables(
        {"customer": parent, "order": child},
        [Corruption("orphan_keys", 0.5, "order", "customer_id")],
        seed=3,
    )
    keys = set(parent.column("customer_id").to_pylist())
    assert len(out.records) == 500
    assert not [r["after"] for r in out.records if r["after"] in keys]


def _orphans(col: pa.Array) -> pa.Table:
    tables = {"t": pa.table({"id": pa.array(range(len(col)), pa.int64()), "x_id": col})}
    out = corrupt_tables(tables, [Corruption("orphan_keys", 0.5, "t", "x_id")], seed=1)
    return out.tables["t"]


def test_orphan_keys_widen_an_integer_column_the_orphans_do_not_fit() -> None:
    """#399: an int32 key near its limit gets int64 orphans instead of an OverflowError."""
    out = _orphans(pa.array([2_000_000_000, 1, 2, 3], pa.int32()))
    assert out.schema.field("x_id").type == pa.int64()
    assert max(out.column("x_id").to_pylist()) > 2_000_000_000


def test_orphan_keys_ignore_infinite_values_when_choosing_the_base() -> None:
    """#399: an infinite float key does not crash the orphan base."""
    out = _orphans(pa.array([1.0, 2.0, float("inf"), 4.0]))
    assert out.num_rows == 4


def test_orphan_keys_on_a_decimal_or_boolean_column_is_a_clear_error() -> None:
    """#399: a column that cannot hold orphan ids is an error naming it and its type."""
    import decimal

    import pytest

    for col in (
        pa.array([decimal.Decimal("1.5")] * 4, pa.decimal128(5, 2)),
        pa.array([True, False, True, False]),
    ):
        with pytest.raises(ValueError, match=r"orphan_keys: t\.x_id is .* integer, float or text"):
            _orphans(col)


def test_negative_amounts_refuses_a_named_unsigned_column() -> None:
    """#401: an unsigned column cannot hold a negative amount, so naming one is an error."""
    import pytest

    t = pa.table({"id": list(range(10)), "u": pa.array(range(1, 11), pa.uint32())})
    with pytest.raises(ValueError, match=r"negative_amounts: t\.u is uint32"):
        corrupt_tables({"t": t}, [Corruption("negative_amounts", 0.5, "t", "u")], seed=1)


def test_negative_amounts_skips_unsigned_columns_it_picks_itself() -> None:
    """#401: the columns picked without a name are those that can hold a negative."""
    t = pa.table(
        {
            "id": list(range(10)),
            "u": pa.array(range(1, 11), pa.uint32()),
            "amount": pa.array([float(i + 1) for i in range(10)]),
        }
    )
    out = corrupt_tables({"t": t}, [Corruption("negative_amounts", 0.5, "t")], seed=1)
    assert {r["column"] for r in out.records} == {"amount"}
    assert all(r["after"] < 0 for r in out.records)


def test_a_corruption_aimed_at_a_table_with_no_fitting_column_is_an_error() -> None:
    """#403: CHAOS.md: a corruption that cannot apply to what it is aimed at is an error."""
    import pytest

    tables = {"t": pa.table({"id": [1, 2, 3]})}
    for kind in ("date_shift", "negative_amounts", "case_whitespace", "orphan_keys"):
        with pytest.raises(ValueError, match=rf"{kind}: table 't' has no column"):
            corrupt_tables(tables, [Corruption(kind, 0.5, "t")], seed=1)


def test_an_untargeted_corruption_that_fits_no_table_is_an_error() -> None:
    """#403: without @TABLE it applies to every table it fits, and fitting none is an error."""
    import pytest

    tables = {"a": pa.table({"id": [1, 2, 3]}), "b": pa.table({"id": [4, 5, 6]})}
    with pytest.raises(ValueError, match=r"date_shift: no table has a column it applies to"):
        corrupt_tables(tables, [Corruption("date_shift", 0.5)], seed=1)


def test_an_untargeted_corruption_still_skips_the_tables_it_does_not_fit() -> None:
    tables = {
        "a": pa.table({"id": [1, 2, 3, 4]}),
        "b": pa.table({"id": [1, 2, 3, 4], "amount": [1.0, 2.0, 3.0, 4.0]}),
    }
    out = corrupt_tables(tables, [Corruption("negative_amounts", 0.5)], seed=1)
    assert {r["table"] for r in out.records} == {"b"}
