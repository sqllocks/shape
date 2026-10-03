"""Regression tests for the HUNT2-io defects (second audit of the io area)."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from shape.builtins.sinks.files import CsvSink, ParquetSink


def _batches(*values: int) -> list[pa.RecordBatch]:
    return pa.table({"a": list(values)}).to_batches()


def _values(folder: Path) -> list[int]:
    out: list[int] = []
    for name in sorted(os.listdir(folder)):
        out += pq.read_table(folder / name).column("a").to_pylist()
    return out


# ---- #624: append picks the next part from the real part number ---------------------------


def test_append_after_a_table_name_that_contains_00001(tmp_path: Path) -> None:
    for v in (1, 2, 3):
        ParquetSink().write(str(tmp_path), "t00001", _batches(v), roll_rows=1, mode="append")
    assert sorted(os.listdir(tmp_path / "t00001")) == [
        "t00001-00001.parquet",
        "t00001-00002.parquet",
        "t00001-00003.parquet",
    ]
    assert _values(tmp_path / "t00001") == [1, 2, 3]


def test_append_with_a_date_that_contains_00001(tmp_path: Path) -> None:
    template = "{table}/{yyyymmdd}-{part}.{ext}"
    for v in (1, 2, 3):
        ParquetSink().write(
            str(tmp_path),
            "t",
            _batches(v),
            roll_rows=1,
            mode="append",
            path_template=template,
            batch_date="2000-01-01",
        )
    assert _values(tmp_path / "t") == [1, 2, 3]


def test_append_continues_after_part_99999(tmp_path: Path) -> None:
    folder = tmp_path / "t"
    folder.mkdir()
    pq.write_table(pa.table({"a": [1]}), folder / "t-99999.parquet")
    for v in (2, 3):
        ParquetSink().write(str(tmp_path), "t", _batches(v), roll_rows=1, mode="append")
    assert sorted(os.listdir(folder)) == ["t-100000.parquet", "t-100001.parquet", "t-99999.parquet"]


def test_append_ignores_files_that_only_look_like_parts(tmp_path: Path) -> None:
    folder = tmp_path / "t"
    folder.mkdir()
    pq.write_table(pa.table({"a": [1]}), folder / "t-00001.parquet")
    (folder / "t-0000x.parquet").write_bytes(b"")
    (folder / "other-00007.parquet").write_bytes(b"")
    ParquetSink().write(str(tmp_path), "t", _batches(2), roll_rows=1, mode="append")
    assert (folder / "t-00002.parquet").exists()


# ---- #626: Delta commit_rows with an explicit schema ---------------------------------------


def test_delta_commit_rows_with_a_schema_option(tmp_path: Path) -> None:
    pytest.importorskip("deltalake")
    from shape.builtins.sinks.delta import DeltaSink

    schema = pa.schema([("a", pa.int64())])
    rows = DeltaSink().write(
        str(tmp_path),
        "t",
        iter([b for v in (1, 2, 3, 4, 5) for b in _batches(v)]),
        schema=schema,
        commit_rows=2,
    )
    assert rows == 5
    from deltalake import DeltaTable

    table = DeltaTable(str(tmp_path / "t"))
    assert sorted(table.to_pyarrow_table().column("a").to_pylist()) == [1, 2, 3, 4, 5]
    assert table.version() >= 1  # several commits


def test_delta_commit_rows_with_a_schema_and_no_batches(tmp_path: Path) -> None:
    pytest.importorskip("deltalake")
    from shape.builtins.sinks.delta import DeltaSink

    schema = pa.schema([("a", pa.int64())])
    assert DeltaSink().write(str(tmp_path), "t", iter([]), schema=schema, commit_rows=2) == 0
    from deltalake import DeltaTable

    assert DeltaTable(str(tmp_path / "t")).to_pyarrow_table().num_rows == 0


# ---- #625: a plain file is replaced whole or not at all -----------------------------------

_PLAIN = [
    ("csv", "keep.csv"),
    ("tsv", "keep.tsv"),
    ("jsonl", "keep.jsonl"),
    ("parquet", "keep.parquet"),
    ("ipc", "keep.arrow"),
]


def _sink(name: str):  # type: ignore[no-untyped-def]
    from shape.builtins import sinks

    return {
        "csv": sinks.CsvSink,
        "tsv": sinks.TsvSink,
        "jsonl": sinks.JsonlSink,
        "parquet": sinks.ParquetSink,
        "ipc": sinks.IpcSink,
    }[name]()


def _failing(*, schema_change: bool = False):  # type: ignore[no-untyped-def]
    yield pa.table({"a": [1, 2]}).to_batches()[0]
    if schema_change:
        yield pa.table({"a": ["x"]}).to_batches()[0]
    else:
        raise RuntimeError("source failed")


@pytest.mark.parametrize(
    ("fmt", "name", "schema_change"),
    [(f, n, False) for f, n in _PLAIN] + [(f, n, True) for f, n in _PLAIN if f != "jsonl"],
)  # JSON Lines has no schema to violate
def test_failed_write_keeps_the_previous_file(
    tmp_path: Path, fmt: str, name: str, schema_change: bool
) -> None:
    target = tmp_path / name
    _sink(fmt).write(str(target), "keep", _batches(7))
    before = target.read_bytes()
    with pytest.raises((RuntimeError, pa.ArrowInvalid)):
        _sink(fmt).write(str(target), "keep", _failing(schema_change=schema_change))
    assert target.read_bytes() == before
    assert os.listdir(tmp_path) == [name]  # no temporary file left behind


@pytest.mark.parametrize(("fmt", "name"), _PLAIN)
def test_failed_first_write_leaves_nothing(tmp_path: Path, fmt: str, name: str) -> None:
    target = tmp_path / name
    with pytest.raises(RuntimeError):
        _sink(fmt).write(str(target), "keep", _failing())
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize(("fmt", "name"), _PLAIN)
def test_successful_write_leaves_only_the_file(tmp_path: Path, fmt: str, name: str) -> None:
    target = tmp_path / name
    assert _sink(fmt).write(str(target), "keep", _batches(1, 2, 3)) == 3
    assert os.listdir(tmp_path) == [name]
    assert _sink(fmt).write(str(target), "keep", _batches(4)) == 1  # replaced, not appended


def test_plain_write_into_a_directory_and_a_long_name(tmp_path: Path) -> None:
    assert CsvSink().write(str(tmp_path) + "/", "t", _batches(1)) == 1
    assert (tmp_path / "t.csv").exists()
    long_name = "x" * 250 + ".csv"
    CsvSink().write(str(tmp_path / long_name), "t", _batches(1))
    assert (tmp_path / long_name).exists()


def test_plain_write_through_a_symlink_and_keeps_the_mode(tmp_path: Path) -> None:
    real = tmp_path / "real.csv"
    real.write_text("old")
    real.chmod(0o640)
    link = tmp_path / "link.csv"
    link.symlink_to(real)
    CsvSink().write(str(link), "t", _batches(1))
    assert link.is_symlink()
    assert real.read_text() == '"a"\n1\n'
    assert (real.stat().st_mode & 0o777) == 0o640


@pytest.mark.skipif(not Path("/dev/null").exists(), reason="needs /dev/null")
def test_plain_write_to_a_device_is_written_in_place() -> None:
    assert CsvSink().write("/dev/null", "t", _batches(1, 2)) == 2


_ = dt
