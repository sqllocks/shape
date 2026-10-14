"""``shape generate`` from a profile with nested columns (GitHub issue #1).

The column comes out with the exact Arrow type the profile recorded, the recorded null rate and,
for a list or a map, lengths drawn from the recorded length distribution. The child values come
from the existing per-type generators, whatever the nesting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import nested_inputs as ni
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.cli.main import main

ROWS = 600


def _generated(table: pa.Table, tmp_path: Path, rows: int = ROWS, seed: int = 11) -> pa.Table:
    prof = shape.profile(str(ni.write_parquet(tmp_path / "t.parquet", table)))
    result = shape.generate(prof, n=rows, seed=seed)
    return result.tables["t"]


def _read_type(tmp_path: Path, name: str) -> pa.DataType:
    """The type of ``name`` as the Parquet reader gives it (a map's value field is named after
    the column there)."""
    return pq.read_schema(str(tmp_path / "t.parquet")).field(name).type


@pytest.mark.parametrize("name", list(ni.NESTED_TYPES))
def test_the_column_has_the_recorded_arrow_type(name: str, tmp_path: Path) -> None:
    out = _generated(ni.table_with(name), tmp_path)
    assert out.schema.field(name).type.equals(_read_type(tmp_path, name))
    out.validate(full=True)
    assert out.num_rows == ROWS


def test_every_nested_type_together_with_flat_columns(tmp_path: Path) -> None:
    out = _generated(ni.table_with(*ni.NESTED_TYPES), tmp_path)
    for name in ni.NESTED_TYPES:
        assert out.schema.field(name).type.equals(_read_type(tmp_path, name))
    out.validate(full=True)


@pytest.mark.parametrize("name", list(ni.NESTED_TYPES))
def test_the_null_rate_is_honoured(name: str, tmp_path: Path) -> None:
    out = _generated(ni.table_with(name), tmp_path)
    assert out.column(name).null_count / ROWS == pytest.approx(0.2, abs=0.07)


@pytest.mark.parametrize("name", ["li", "ls", "mp", "lst"])
def test_lengths_follow_the_recorded_distribution(name: str, tmp_path: Path) -> None:
    out = _generated(ni.table_with(name), tmp_path)
    source = [len(v) for v in ni.nested_array(name).to_pylist() if v is not None]
    got = [len(v) for v in out.column(name).to_pylist() if v is not None]
    assert min(got) >= min(source) and max(got) <= max(source)
    assert sum(got) / len(got) == pytest.approx(sum(source) / len(source), abs=0.3)
    assert set(got) == set(source)  # every recorded length shows up in 480 rows


def test_map_keys_are_unique_within_a_map(tmp_path: Path) -> None:
    out = _generated(ni.table_with("mp"), tmp_path)
    for value in out.column("mp").to_pylist():
        if value is not None:
            keys = [k for k, _ in value]
            assert len(keys) == len(set(keys))


def test_generation_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    a = _generated(ni.table_with("li", "mp", "ss"), tmp_path, seed=5)
    b = _generated(ni.table_with("li", "mp", "ss"), tmp_path, seed=5)
    c = _generated(ni.table_with("li", "mp", "ss"), tmp_path, seed=6)
    assert a.equals(b)
    assert not a.equals(c)


def test_flat_columns_keep_their_types_beside_a_nested_column(tmp_path: Path) -> None:
    with_nested = _generated(ni.table_with("st"), tmp_path)
    flat = _generated(ni.flat_table(), tmp_path)
    for name in ni.flat_table().column_names:
        assert with_nested.schema.field(name).type == flat.schema.field(name).type


def test_the_cli_writes_the_nested_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = ni.write_parquet(tmp_path / "t.parquet", ni.table_with("li", "st", "mp"))
    assert main(["profile", str(src), "-o", str(tmp_path / "t.shape")]) == 0
    out_dir = tmp_path / "out"
    code = main(
        ["generate", "--from", str(tmp_path / "t.shape"), "--rows", "50", "-o", str(out_dir),
         "--format", "parquet"]
    )  # fmt: skip
    assert code == 0, capsys.readouterr().err
    written = pq.read_table(str(out_dir / "t.parquet"))
    for name in ("li", "st", "mp"):
        assert written.schema.field(name).type.equals(_read_type(tmp_path, name))


@pytest.fixture(params=["python", "rust"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    from shape.kernel import dispatch

    dispatch.reset()
    yield request.param
    dispatch.reset()


_BYTES_TABLE = pa.table(
    {
        "id": list(range(40)),
        "bl": pa.array([[b"ab", b"c"]] * 40, pa.list_(pa.binary())),
        "lb": pa.array([[b"abc"]] * 40, pa.list_(pa.large_binary())),
        "fb": pa.array([[b"abcd"]] * 40, pa.list_(pa.binary(4))),
        "du": pa.array([[1, 2]] * 40, pa.list_(pa.duration("ms"))),
        "st": pa.array(
            [{"b": b"x", "d": 5}] * 40, pa.struct([("b", pa.binary()), ("d", pa.duration("us"))])
        ),
    }
)


def test_bytes_and_durations_are_generated_inside_nested_columns(
    kernel: str, tmp_path: Path
) -> None:
    out = _generated(_BYTES_TABLE, tmp_path, rows=200)
    src = pq.read_schema(str(tmp_path / "t.parquet"))
    for name in ("bl", "lb", "fb", "du", "st"):
        assert out.schema.field(name).type.equals(src.field(name).type)
    out.validate(full=True)
    assert {len(b) for v in out.column("fb").to_pylist() if v for b in v} == {4}
    lens = {len(b) for v in out.column("bl").to_pylist() if v for b in v}
    assert min(lens) >= 1 and max(lens) <= 16 and len(lens) > 3
    spans = [d for v in out.column("du").to_pylist() if v for d in v]
    assert all(0 <= d.total_seconds() < 86_400 for d in spans) and len(set(spans)) > 10


def test_bytes_are_the_same_on_both_kernels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("shape._kernel")
    from shape.kernel import dispatch

    tables = []
    for mode in ("python", "rust"):
        monkeypatch.setenv("SHAPE_KERNEL", mode)
        dispatch.reset()
        tables.append(_generated(_BYTES_TABLE, tmp_path, rows=100, seed=4))
    dispatch.reset()
    assert tables[0].equals(tables[1])


@pytest.mark.parametrize(
    "child",
    [
        pa.dense_union([pa.field("a", pa.int8())]),
        pa.sparse_union([pa.field("a", pa.int8())]),
    ],
    ids=["dense_union", "sparse_union"],
)
def test_a_child_type_with_no_generator_is_refused_by_name(
    child: pa.DataType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from shape.generation.nested import check_generatable
    from shape.profile.nested import arrow_type_to_spec

    spec = {"arrow_type_spec": arrow_type_to_spec(pa.list_(child))}
    with pytest.raises(ValueError) as err:
        check_generatable(spec, "t.bx")
    assert "t.bx" in str(err.value) and "union" in str(err.value)


def test_chunking_does_not_change_the_table(tmp_path: Path) -> None:
    from shape.generation.engine import Engine
    from shape.generation.fit import PRESET, fit_schema

    prof = shape.profile(
        str(ni.write_parquet(tmp_path / "t.parquet", ni.table_with(*ni.NESTED_TYPES)))
    )
    fitted = fit_schema(prof, rows=150)
    tables = [
        Engine(fitted.schema, scale=PRESET, seed=3, chunk_rows=chunk).generate().tables["t"]
        for chunk in (65_536, 37, 1)
    ]
    assert tables[0].equals(tables[1]) and tables[0].equals(tables[2])


def test_a_delta_table_holds_the_nested_columns(tmp_path: Path) -> None:
    src = ni.write_parquet(tmp_path / "t.parquet", ni.table_with("li", "mp", "lst"))
    assert main(["profile", str(src), "-o", str(tmp_path / "t.shape")]) == 0
    code = main(["generate", "--from", str(tmp_path / "t.shape"), "--rows", "30", "-o",
                 str(tmp_path / "out"), "--format", "delta"])  # fmt: skip
    assert code == 0
    from deltalake import DeltaTable

    from shape.io import deltafs

    back = deltafs.local_table(DeltaTable(str(tmp_path / "out" / "t")), tmp_path / "out" / "t")
    assert back.num_rows == 30
    assert back.schema.field("li").type.equals(_read_type(tmp_path, "li"))


@pytest.mark.parametrize("fmt", ["csv", "tsv", "sql"])
def test_a_format_of_flat_cells_refuses_a_nested_column_by_name(
    fmt: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = ni.write_parquet(tmp_path / "t.parquet", ni.table_with("li"))
    assert main(["profile", str(src), "-o", str(tmp_path / "t.shape")]) == 0
    capsys.readouterr()
    code = main(["generate", "--from", str(tmp_path / "t.shape"), "--rows", "5", "-o",
                 str(tmp_path / "out"), "--format", fmt])  # fmt: skip
    err = capsys.readouterr().err
    assert code == 2
    assert "t.li is a nested column" in err and fmt in err
    assert not (tmp_path / "out" / f"t.{fmt}").exists()


def test_the_fitted_schema_validates_without_warnings(tmp_path: Path) -> None:
    from shape.generation.fit import fit_schema

    prof = shape.profile(
        str(ni.write_parquet(tmp_path / "t.parquet", ni.table_with(*ni.NESTED_TYPES)))
    )
    issues = [i for i in fit_schema(prof).schema.validate() if "nested" in i.message]
    assert issues == []


def test_null_map_values_are_drawn_at_the_recorded_rate(tmp_path: Path) -> None:
    rows = [[("a", None), ("b", 2)] if i % 2 == 0 else [("c", 3), ("d", 4)] for i in range(200)]
    table = pa.table(
        {"id": list(range(200)), "m": pa.array(rows, pa.map_(pa.string(), pa.int64()))}
    )
    prof = shape.profile(str(ni.write_parquet(tmp_path / "t.parquet", table)))
    assert prof.to_dict()["columns"]["m"]["nested"]["element_null_rate"] == 0.25
    out = shape.generate(prof, n=400, seed=3).tables["t"]
    items = [v for row in out.column("m").to_pylist() if row for _, v in row]
    assert 0.15 < sum(v is None for v in items) / len(items) < 0.35


def test_unsigned_64_bit_map_keys_are_generated(tmp_path: Path) -> None:
    rows = [[(2**63 + i, "x"), (i, "y")] for i in range(40)]
    table = pa.table(
        {"id": list(range(40)), "m": pa.array(rows, pa.map_(pa.uint64(), pa.string()))}
    )
    out = _generated(table, tmp_path, rows=100)
    assert out.schema.field("m").type.key_type == pa.uint64()
    for row in out.column("m").to_pylist():
        if row:
            keys = [k for k, _ in row]
            assert len(keys) == len(set(keys))


def test_a_few_long_lists_do_not_cost_rows_times_the_longest(tmp_path: Path) -> None:
    import time

    lists = [[1] for _ in range(1999)] + [list(range(3000))]
    table = pa.table({"id": list(range(2000)), "li": pa.array(lists, pa.list_(pa.int64()))})
    prof = shape.profile(str(ni.write_parquet(tmp_path / "t.parquet", table)))
    start = time.perf_counter()
    out = shape.generate(prof, n=100_000, seed=1).tables["t"]
    took = time.perf_counter() - start
    assert out.num_rows == 100_000
    assert took < 5.0, f"{took:.1f}s: the cost follows the number of elements, not rows x longest"
