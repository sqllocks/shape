"""``shape diff`` on profiles with nested columns (GitHub issue #1): an unchanged nested column is
no drift; a changed null rate, a changed Arrow type or a shifted list length is reported on that
column, and the flat columns are compared as before."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import nested_inputs as ni
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

import shape


def _profile(table: pa.Table, directory: Path, stem: str) -> Any:
    return shape.profile(str(ni.write_parquet(directory / f"{stem}.parquet", table)))


def _kinds(result: Any, column: str) -> dict[str, dict[str, Any]]:
    return {c["kind"]: c for c in result.to_dict()["changes"] if c["column"] == column}


@pytest.mark.parametrize("name", list(ni.NESTED_TYPES))
def test_the_same_profile_has_no_drift(name: str, tmp_path: Path) -> None:
    a = _profile(ni.table_with(name), tmp_path, "a")
    b = _profile(ni.table_with(name), tmp_path, "b")
    result = shape.diff(a, b)
    assert not result.drifted
    assert result.to_dict()["changes"] == []


def test_a_nested_column_that_gains_nulls(tmp_path: Path) -> None:
    base = ni.table_with("li")
    arr = ni.nested_array("li").to_pylist()
    more = [None if i % 2 == 0 else v for i, v in enumerate(arr)]
    cur = base.set_column(
        base.schema.get_field_index("li"), "li", pa.array(more, pa.list_(pa.int64()))
    )
    result = shape.diff(_profile(base, tmp_path, "a"), _profile(cur, tmp_path, "b"))
    change = _kinds(result, "li")["null_rate_change"]
    assert change["baseline"] == pytest.approx(0.2)
    assert change["current"] == pytest.approx(0.6)  # every other row, and the rows already null


def test_a_changed_arrow_type_is_a_breaking_dtype_change(tmp_path: Path) -> None:
    base = ni.table_with("li")
    ints = base.column("li")
    strs = pa.array(
        [None if v is None else [str(x) for x in v] for v in ints.to_pylist()],
        pa.list_(pa.string()),
    )
    cur = base.set_column(base.schema.get_field_index("li"), "li", strs)
    result = shape.diff(_profile(base, tmp_path, "a"), _profile(cur, tmp_path, "b"))
    change = _kinds(result, "li")["dtype_change"]
    assert change["baseline"] == "list<int64>"
    assert change["current"] == "list<string>"
    assert change["class"] == "breaking"


def test_a_flat_column_that_becomes_nested_is_a_dtype_change(tmp_path: Path) -> None:
    base = ni.table_with()
    cur = base.append_column("amount2", ni.nested_array("li")).drop_columns(["label"])
    cur = cur.rename_columns([("label" if n == "amount2" else n) for n in cur.column_names])
    result = shape.diff(_profile(base, tmp_path, "a"), _profile(cur, tmp_path, "b"))
    change = _kinds(result, "label")["dtype_change"]
    assert change["baseline"] == "string"
    assert change["current"] == "list<int64>"


def test_a_shift_in_list_lengths_is_reported(tmp_path: Path) -> None:
    base = ni.table_with("ls")
    longer = pa.array(
        [None if v is None else v + v + v for v in ni.nested_array("ls").to_pylist()],
        pa.list_(pa.string()),
    )
    cur = base.set_column(base.schema.get_field_index("ls"), "ls", longer)
    result = shape.diff(_profile(base, tmp_path, "a"), _profile(cur, tmp_path, "b"))
    change = _kinds(result, "ls")["length_change"]
    assert change["current"] == pytest.approx(3 * change["baseline"], rel=0.01)


def test_flat_columns_are_compared_as_before(tmp_path: Path) -> None:
    flat_a = _profile(ni.flat_table(), tmp_path, "fa")
    changed = ni.flat_table().set_column(
        2, "label", pa.array([None] * 30 + ["x"] * 30, pa.string())
    )
    flat_b = _profile(changed, tmp_path, "fb")
    plain = shape.diff(flat_a, flat_b).to_dict()["changes"]
    with_nested_a = _profile(ni.table_with("li", "st"), tmp_path, "na")
    nested_b = ni.table_with("li", "st").set_column(
        2, "label", pa.array([None] * 30 + ["x"] * 30, pa.string())
    )
    mixed = shape.diff(with_nested_a, _profile(nested_b, tmp_path, "nb")).to_dict()["changes"]
    assert [c for c in mixed if c["column"] == "label"] == [
        c for c in plain if c["column"] == "label"
    ]
    assert not [c for c in mixed if c["column"] in ("li", "st")]
