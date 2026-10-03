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
        iter(_batches(1, 2, 3, 4, 5)),
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


_ = (CsvSink, dt)
