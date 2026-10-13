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


# A child type with no generator (binary, duration, union ...) is reported to the lead; until the
# lead chooses a behaviour, `generate` refuses it by name instead of writing a wrong column.
@pytest.mark.parametrize("child", [pa.binary(), pa.duration("us")], ids=["binary", "duration"])
def test_a_child_type_without_a_generator_is_refused_by_name(
    child: pa.DataType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values: list[Any] = [[b"a"] if pa.types.is_binary(child) else [1]] * 20
    table = pa.table({"id": list(range(20)), "bx": pa.array(values, pa.list_(child))})
    src = ni.write_parquet(tmp_path / "t.parquet", table)
    assert main(["profile", str(src), "-o", str(tmp_path / "t.shape")]) == 0
    code = main(["generate", "--from", str(tmp_path / "t.shape"), "--rows", "5", "-o",
                 str(tmp_path / "out"), "--format", "parquet"])  # fmt: skip
    err = capsys.readouterr().err
    assert code == 2
    lines = [x for x in err.splitlines() if x.startswith("shape: error:")]
    assert len(lines) == 1 and "bx" in lines[0] and str(child) in lines[0], err


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
