"""AUD-chaos regressions for the targeted corruptions and the ground-truth log."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from shape.chaos.groundtruth import (
    Corruption,
    corrupt_tables,
    read_ground_truth,
    write_ground_truth,
)


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


def test_a_corruption_aimed_at_an_empty_table_with_fitting_columns_changes_nothing() -> None:
    """#403: an empty batch is not an error; its columns still fit."""
    empty = pa.table({"id": pa.array([], pa.int64()), "status": pa.array([], pa.string())})
    out = corrupt_tables({"t": empty}, [Corruption("case_whitespace", 0.5, "t")], seed=1)
    assert out.records == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"seed": -1}, r"the seed is an integer 0 or more, got -1"),
        ({"seed": 1, "batch": -1}, r"the batch is an integer 0 or more, got -1"),
    ],
)
def test_corrupt_tables_names_a_bad_seed_or_batch(kwargs: dict[str, int], message: str) -> None:
    """#408: the error names the seed or the batch."""
    tables = {"t": pa.table({"id": [1, 2, 3]})}
    with pytest.raises(ValueError, match=message):
        corrupt_tables(tables, [Corruption("duplicates", 0.5, "t")], **kwargs)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("duplicates=0.1@t:from=x", r"option from .* an integer, got 'x'"),
        ("duplicates=0.1@t:to=", r"option to .* an integer, got ''"),
        ("date_shift=0.1@t:days=1.5", r"option days .* an integer, got '1.5'"),
        ("null_creep=0.1@t.c:step=lots", r"option step .* a number, got 'lots'"),
    ],
)
def test_corruption_parse_names_the_bad_option(text: str, message: str) -> None:
    """#408: a bad option names itself."""
    with pytest.raises(ValueError, match=message):
        Corruption.parse(text)


def test_read_ground_truth_refuses_an_unknown_log_version(tmp_path: Path) -> None:
    """#410: a log of a version this release does not know is refused, naming the version."""
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"record": "run", "log_version": 2}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"log_version 2.*reads version 1"):
        read_ground_truth(log)


def test_read_ground_truth_names_a_malformed_line(tmp_path: Path) -> None:
    """#410: a line that is not JSON is reported with its number."""
    log = tmp_path / "log.jsonl"
    log.write_text(
        json.dumps({"record": "run", "log_version": 1}) + "\n{not json\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"log\.jsonl line 2 is not JSON"):
        read_ground_truth(log)


def test_read_ground_truth_round_trips_a_written_log(tmp_path: Path) -> None:
    tables = {"t": pa.table({"id": [1, 2, 3, 4]})}
    out = corrupt_tables(tables, [Corruption("duplicates", 0.5, "t")], seed=1)
    run, changes = read_ground_truth(write_ground_truth(tmp_path / "g.jsonl", out))
    assert run["log_version"] == 1
    assert len(changes) == 2
