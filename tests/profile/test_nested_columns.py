"""What the profile records for a nested column (GitHub issue #1).

A list, struct or map column is recorded with its exact Arrow type, the table's row count, its
null count and rate and, for a list or a map, the distribution of its lengths. The statistics of
the flat columns do not depend on a nested column being present. Both kernels agree.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import nested_inputs as ni
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.kernel import dispatch
from shape.profile.reference.profile import save


@pytest.fixture(params=["python", "rust"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


def _profile(table: pa.Table, tmp_path: Path, kind: str = "parquet") -> dict[str, Any]:
    src = ni.write(kind, tmp_path, table)
    return shape.profile(str(src)).to_dict()


@pytest.mark.parametrize("name", list(ni.NESTED_TYPES))
def test_the_exact_arrow_type_is_recorded(kernel: str, name: str, tmp_path: Path) -> None:
    from shape.profile.nested import arrow_type_from_spec

    src = ni.write("parquet", tmp_path, ni.table_with(name))
    col = shape.profile(str(src)).to_dict()["columns"][name]
    want = pq.read_schema(str(src)).field(name).type  # the type the reader gives the column
    assert col["dtype"] == "nested"
    assert col["arrow_type"] == str(want)
    got = arrow_type_from_spec(json.loads(json.dumps(col["arrow_type_spec"])))  # via plain JSON
    assert got.equals(want)


@pytest.mark.parametrize("name", list(ni.NESTED_TYPES))
def test_row_count_and_null_rate(kernel: str, name: str, tmp_path: Path) -> None:
    doc = _profile(ni.table_with(name), tmp_path)
    col = doc["columns"][name]
    assert doc["row_count"] == ni.ROWS
    assert col["nested"]["rows"] == ni.ROWS
    assert col["null_count"] == 12
    assert col["null_rate"] == pytest.approx(12 / ni.ROWS)
    assert col["nested"]["non_null"] == ni.ROWS - 12


def _lengths(name: str) -> list[int]:
    arr = ni.nested_array(name)
    return [len(v) for v in arr.to_pylist() if v is not None]


@pytest.mark.parametrize("name", ["li", "ls", "mp", "lst"])
def test_lists_and_maps_have_a_length_distribution(kernel: str, name: str, tmp_path: Path) -> None:
    length = _profile(ni.table_with(name), tmp_path)["columns"][name]["nested"]["length"]
    lens = _lengths(name)
    assert length["min"] == min(lens)
    assert length["max"] == max(lens)
    assert length["mean"] == pytest.approx(sum(lens) / len(lens), abs=0.01)
    assert length["hist"] == {str(k): lens.count(k) for k in sorted(set(lens))}
    assert {"std", "p50", "p95"} <= set(length)


@pytest.mark.parametrize("name", ["st", "ss"])
def test_a_struct_has_no_length(kernel: str, name: str, tmp_path: Path) -> None:
    nested = _profile(ni.table_with(name), tmp_path)["columns"][name]["nested"]
    assert nested["kind"] == "struct"
    assert "length" not in nested


@pytest.mark.parametrize("name", list(ni.NESTED_TYPES))
def test_flat_columns_do_not_change(kernel: str, name: str, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    alone = _profile(ni.flat_table(), tmp_path / "a")["columns"]
    mixed = _profile(ni.table_with(name), tmp_path / "b")["columns"]
    for col in ni.flat_table().column_names:
        assert json.dumps(mixed[col], sort_keys=True) == json.dumps(alone[col], sort_keys=True)


def test_the_kernels_record_the_same_nested_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("shape._kernel")
    src = ni.write("parquet", tmp_path, ni.table_with(*ni.NESTED_TYPES))
    docs = []
    for mode in ("python", "rust"):
        monkeypatch.setenv("SHAPE_KERNEL", mode)
        dispatch.reset()
        docs.append(json.dumps(shape.profile(str(src)).to_dict(), sort_keys=True))
    dispatch.reset()
    assert docs[0] == docs[1]


@pytest.mark.filterwarnings("ignore:.*is not signed")
def test_a_nested_column_survives_the_artifact_round_trip(kernel: str, tmp_path: Path) -> None:
    src = ni.write("parquet", tmp_path, ni.table_with("li", "mp"))
    prof = shape.profile(str(src))
    path = tmp_path / "p.shape"
    save(prof, str(path))
    again = shape.load(str(path)).to_dict()
    assert again["columns"]["li"] == prof.to_dict()["columns"]["li"]


def test_a_table_without_nested_columns_gets_no_new_keys(kernel: str, tmp_path: Path) -> None:
    for col in _profile(ni.flat_table(), tmp_path)["columns"].values():
        assert "arrow_type" not in col and "arrow_type_spec" not in col and "nested" not in col


def test_a_column_of_only_nulls_in_a_nested_type(kernel: str, tmp_path: Path) -> None:
    table = pa.table({"id": [1, 2, 3], "li": pa.array([None, None, None], pa.list_(pa.int64()))})
    col = _profile(table, tmp_path)["columns"]["li"]
    assert col["null_rate"] == 1.0
    assert col["nested"]["non_null"] == 0
    assert "length" not in col["nested"] or col["nested"]["length"] is None


def test_an_empty_table_with_a_nested_column(kernel: str, tmp_path: Path) -> None:
    table = pa.table({"id": pa.array([], pa.int64()), "li": pa.array([], pa.list_(pa.int64()))})
    src = ni.write("parquet", tmp_path, table)
    col = shape.profile(str(src)).to_dict()["columns"]["li"]
    assert col["arrow_type"] == str(pq.read_schema(str(src)).field("li").type)
    assert col["null_rate"] in (None, 0.0)


def test_profiles_of_parts_merge_with_their_nested_columns(tmp_path: Path) -> None:
    from shape.profile.merge import merge_profiles

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = shape.profile(str(ni.write("parquet", tmp_path / "a", ni.table_with("li", "mp"))))
    b = shape.profile(str(ni.write("parquet", tmp_path / "b", ni.table_with("li", "mp"))))
    merged = merge_profiles([a, b], exact_only=True).to_dict()
    col = merged["columns"]["li"]
    assert merged["row_count"] == 2 * ni.ROWS
    assert col["null_count"] == 24
    assert col["nested"]["rows"] == 2 * ni.ROWS and col["nested"]["non_null"] == 2 * 48
    lens = _lengths("li")
    assert col["nested"]["length"]["hist"] == {str(k): 2 * lens.count(k) for k in sorted(set(lens))}
    assert col["arrow_type"] == a.to_dict()["columns"]["li"]["arrow_type"]


def test_profiles_of_parts_with_different_nested_types_do_not_merge(tmp_path: Path) -> None:
    from shape.profile.merge import MergeError, merge_profiles

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = shape.profile(str(ni.write("parquet", tmp_path / "a", ni.table_with("li"))))
    ls = ni.table_with("ls").rename_columns(["id", "amount", "label", "flag", "li"])
    b = shape.profile(str(ni.write("parquet", tmp_path / "b", ls)))
    with pytest.raises(MergeError, match="li"):
        merge_profiles([a, b], exact_only=True)


def test_a_code_validator_refuses_a_nested_column_by_name(tmp_path: Path) -> None:
    src = ni.write("parquet", tmp_path, ni.table_with("li"))
    with pytest.raises(ValueError, match="column 'li' is nested"):
        shape.profile(str(src), validators={"li": "iban"})
