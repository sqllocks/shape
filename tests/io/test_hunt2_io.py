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
    [(f, n, False) for f, n in _PLAIN]
    + [(f, n, True) for f, n in _PLAIN if f in ("parquet", "ipc")],
)  # text formats have no schema to violate
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


# ---- #623: folders that start with _ or . are skipped as a whole ----------------------------


def _tree(root: Path) -> None:
    t = pa.table({"a": [1, 2]})
    for sub in ("", "_shape_tmp", ".hidden", "_delta_log", "keep/deeper"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    pq.write_table(t, root / "p.parquet")
    pq.write_table(t, root / "keep" / "deeper" / "q.parquet")
    pq.write_table(t, root / "_shape_tmp" / "abc-p.parquet")
    pq.write_table(t, root / ".hidden" / "q.parquet")
    pq.write_table(pa.table({"z": [1]}), root / "_delta_log" / "0.checkpoint.parquet")
    pq.write_table(t, root / "_skip.parquet")


def test_directory_read_skips_hidden_and_underscore_folders(tmp_path: Path) -> None:
    from shape.io import expand_paths, read_table

    _tree(tmp_path)
    found = [str(p.relative_to(tmp_path)) for p in expand_paths(tmp_path)]
    assert found == ["keep/deeper/q.parquet", "p.parquet"]
    assert read_table(tmp_path).num_rows == 4


def test_a_directory_named_with_an_underscore_can_still_be_the_root(tmp_path: Path) -> None:
    from shape.io import expand_paths

    root = tmp_path / "_landing"
    _tree(root)
    found = [str(p.relative_to(root)) for p in expand_paths(root)]
    assert found == ["keep/deeper/q.parquet", "p.parquet"]


def test_abfss_source_skips_hidden_and_underscore_folders() -> None:
    import io as _io

    fsspec = pytest.importorskip("fsspec")
    from shape.builtins.sources.azure import _list_files

    fs = fsspec.filesystem("memory")
    buffer = _io.BytesIO()
    pq.write_table(pa.table({"a": [1]}), buffer)
    for path in (
        "/hunt2/root/part-1.parquet",
        "/hunt2/root/sub/part-2.parquet",
        "/hunt2/root/_shape_tmp/abc-part-3.parquet",
        "/hunt2/root/.hidden/part-4.parquet",
        "/hunt2/root/_delta_log/0.checkpoint.parquet",
    ):
        with fs.open(path, "wb") as handle:
            handle.write(buffer.getvalue())
    try:
        assert _list_files(fs, "hunt2/root") == [
            "/hunt2/root/part-1.parquet",
            "/hunt2/root/sub/part-2.parquet",
        ]
        # a root that is itself below an underscore folder is still read
        assert _list_files(fs, "hunt2/root/_delta_log") == [
            "/hunt2/root/_delta_log/0.checkpoint.parquet"
        ]
    finally:
        fs.rm("/hunt2", recursive=True)


# ---- #627: Delta overwrite replaces the schema too -----------------------------------------


def _delta_rows(path: Path) -> dict[str, list[object]]:
    from deltalake import DeltaTable

    return DeltaTable(str(path)).to_pyarrow_table().sort_by("a").to_pydict()


def test_delta_overwrite_with_a_changed_schema(tmp_path: Path) -> None:
    pytest.importorskip("deltalake")
    from deltalake import DeltaTable

    from shape.builtins.sinks.delta import DeltaSink

    sink = DeltaSink()
    sink.write(str(tmp_path), "t", pa.table({"a": [1, 2]}).to_batches())
    new = pa.table({"a": [3], "b": ["x"]})
    assert sink.write(str(tmp_path), "t", new.to_batches()) == 1
    assert _delta_rows(tmp_path / "t") == {"a": [3], "b": ["x"]}
    protocol = DeltaTable(str(tmp_path / "t")).protocol()
    assert (protocol.min_reader_version, protocol.min_writer_version) == (1, 2)
    assert not protocol.reader_features and not protocol.writer_features
    # and a column removed or retyped
    sink.write(str(tmp_path), "t", pa.table({"a": ["s"]}).to_batches())
    assert DeltaTable(str(tmp_path / "t")).to_pyarrow_table().schema.names == ["a"]


def test_delta_overwrite_in_micro_batches_replaces_the_schema_once(tmp_path: Path) -> None:
    pytest.importorskip("deltalake")
    from shape.builtins.sinks.delta import DeltaSink

    sink = DeltaSink()
    sink.write(str(tmp_path), "t", pa.table({"a": [9]}).to_batches())
    batches = [b for v in (1, 2, 3) for b in pa.table({"a": [v], "b": ["x"]}).to_batches()]
    assert sink.write(str(tmp_path), "t", iter(batches), commit_rows=1) == 3
    assert _delta_rows(tmp_path / "t") == {"a": [1, 2, 3], "b": ["x", "x", "x"]}


def test_delta_append_still_refuses_a_different_schema(tmp_path: Path) -> None:
    pytest.importorskip("deltalake")
    from shape.builtins.sinks.delta import DeltaSink

    sink = DeltaSink()
    sink.write(str(tmp_path), "t", pa.table({"a": [1]}).to_batches())
    with pytest.raises(Exception, match="(?i)schema"):
        sink.write(str(tmp_path), "t", pa.table({"a": [2], "b": ["x"]}).to_batches(), mode="append")
    assert _delta_rows(tmp_path / "t") == {"a": [1]}


# ---- #628: delta+abfss reads the documented AZURE_STORAGE_* variables -------------------------

_DELTA_URI = "delta+abfss://c@acct.dfs.core.windows.net/Tables"


def test_delta_cloud_location_uses_the_storage_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shape.builtins.sinks.delta import _location

    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "the-key")
    location, storage = _location(_DELTA_URI, "t", {})
    assert location == "abfss://c@acct.dfs.core.windows.net/Tables/t"
    assert storage == {"azure_storage_account_key": "the-key"}


def test_delta_cloud_location_uses_the_sas_token_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shape.builtins.sinks.delta import _location

    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sig=1")
    assert _location(_DELTA_URI, "t", {})[1] == {"azure_storage_sas_key": "sig=1"}


def test_delta_cloud_location_prefers_an_explicit_credential_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shape.builtins.sinks.delta import _location

    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "from-env")
    assert _location(_DELTA_URI, "t", {"account_key": "explicit"})[1] == {
        "azure_storage_account_key": "explicit"
    }
    storage = _location(_DELTA_URI, "t", {"token": "tok"})[1]
    assert storage == {"azure_storage_token": "tok"}


def test_delta_cloud_location_says_a_connection_string_cannot_open_a_delta_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shape.builtins.sinks.delta import _location

    for name in ("AZURE_STORAGE_ACCOUNT_KEY", "AZURE_STORAGE_SAS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
    with pytest.raises(ValueError, match="connection string"):
        _location(_DELTA_URI, "t", {})


# ---- #629: the single-table Excel sink stores text as text ----------------------------------


def _sheet_cells(path: Path) -> tuple[str, list[list[tuple[object, str]]]]:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    return ws.title, [[(c.value, c.data_type) for c in row] for row in ws.iter_rows()]


def test_excel_sink_stores_formula_like_text_as_text(tmp_path: Path) -> None:
    from shape.builtins.sinks.excel import ExcelSink

    t = pa.table({"a": ["=1+1", "#N/A", "@SUM(1)", "+1", "ok"], "=h": [1, 2, 3, 4, 5]})
    ExcelSink().write(str(tmp_path / "x.xlsx"), "x", t.to_batches())
    _, rows = _sheet_cells(tmp_path / "x.xlsx")
    assert rows[0] == [("a", "s"), ("=h", "s")]
    assert [r[0] for r in rows[1:]] == [
        ("=1+1", "s"),
        ("#N/A", "s"),
        ("@SUM(1)", "s"),
        ("+1", "s"),
        ("ok", "s"),
    ]
    assert [r[1] for r in rows[1:]] == [(1, "n"), (2, "n"), (3, "n"), (4, "n"), (5, "n")]


def test_excel_sink_removes_characters_a_worksheet_cannot_hold(tmp_path: Path) -> None:
    from shape.builtins.sinks.excel import ExcelSink

    t = pa.table({"a": ["a\x01b", "tab\there", "é\u2028x"]})
    assert ExcelSink().write(str(tmp_path / "x.xlsx"), "x", t.to_batches()) == 3
    _, rows = _sheet_cells(tmp_path / "x.xlsx")
    assert [r[0][0] for r in rows[1:]] == ["ab", "tab\there", "é\u2028x"]


@pytest.mark.parametrize(
    ("table", "sheet"),
    [
        ("a/b", "a_b"),
        ("a:b*c?", "a_b_c_"),
        ("[x]", "_x_"),
        ("'quoted'", "quoted"),
        ("x" * 40, "x" * 31),
        ("", "Sheet"),
    ],
)
def test_excel_sink_makes_the_table_name_a_valid_sheet_name(
    tmp_path: Path, table: str, sheet: str
) -> None:
    from shape.builtins.sinks.excel import ExcelSink

    target = tmp_path / "x.xlsx"
    ExcelSink().write(str(target), table, pa.table({"a": [1]}).to_batches())
    assert _sheet_cells(target)[0] == sheet


def test_excel_sink_keeps_a_plain_table_name_and_empty_tables(tmp_path: Path) -> None:
    from shape.builtins.sinks.excel import ExcelSink

    target = tmp_path / "x.xlsx"
    assert (
        ExcelSink().write(str(target), "orders", iter([]), schema=pa.schema([("a", pa.int64())]))
        == 0
    )
    title, rows = _sheet_cells(target)
    assert title == "orders"
    assert rows == [[("a", "s")]]


_ = dt
