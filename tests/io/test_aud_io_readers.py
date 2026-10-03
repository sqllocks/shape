"""AUD-io: regression tests for the readers (issues filed by the io audit)."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.io import CsvOptions, ReaderError, expand_paths, iter_rows, open_source, read_table


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_489_an_existing_file_with_glob_characters_in_its_name_is_read(tmp_path):
    f = _write(tmp_path / "data[1].csv", "a,b\n1,2\n")
    assert expand_paths(f) == [f]
    assert expand_paths(str(f)) == [f]
    assert read_table(str(f)).to_pylist() == [{"a": 1, "b": 2}]
    # a pattern that is not an existing file is still a glob
    _write(tmp_path / "x1.csv", "a\n1\n")
    assert [p.name for p in expand_paths(str(tmp_path / "x[0-9].csv"))] == ["x1.csv"]
    with pytest.raises(FileNotFoundError, match="no files match"):
        expand_paths(str(tmp_path / "nothing[0-9].csv"))


def test_492_a_key_first_seen_after_the_first_batch_is_kept():
    rows = [{"a": 1}, {"a": 2, "b": 3}, {"a": 4, "b": 5}]
    t = read_table(iter(rows), batch_size=1)
    assert t.column_names == ["a", "b"]
    assert t.to_pylist() == [{"a": 1, "b": None}, {"a": 2, "b": 3}, {"a": 4, "b": 5}]
    assert str(t.schema.field("b").type) == "int64"
    assert list(iter_rows(iter(rows), batch_size=1)) == [
        {"a": 1},
        {"a": 2, "b": 3},
        {"a": 4, "b": 5},
    ]
    # a column that is empty in the first batch takes the type of its first values
    t = read_table(iter([{"x": None}, {"x": 1}, {"x": 2}]), batch_size=1)
    assert str(t.schema.field("x").type) == "int64" and t["x"].to_pylist() == [None, 1, 2]


@pytest.mark.parametrize("stream", [False, True])
def test_493_a_column_empty_in_the_first_file_takes_its_type_from_a_later_file(tmp_path, stream):
    a = _write(tmp_path / "a.csv", "k,opt\n1,\n2,\n")
    b = _write(tmp_path / "b.csv", "k,opt\n3,\n")
    c = _write(tmp_path / "c.csv", "k,opt\n4,7\n")
    src = open_source([a, b, c], csv=CsvOptions(stream=stream))
    assert str(src.schema.field("opt").type) == "int64"
    t = src.table()
    assert t["opt"].to_pylist() == [None, None, None, 7]
    assert str(t.schema.field("opt").type) == "int64"


def test_493_jsonl_and_parquet_null_columns_in_the_first_file(tmp_path):
    a = _write(tmp_path / "a.jsonl", '{"k": 1, "opt": null}\n')
    b = _write(tmp_path / "b.jsonl", '{"k": 2, "opt": "x"}\n')
    assert read_table([a, b])["opt"].to_pylist() == [None, "x"]
    pq.write_table(pa.table({"k": [1], "opt": pa.nulls(1)}), tmp_path / "a.parquet")
    pq.write_table(pa.table({"k": [2], "opt": [2.5]}), tmp_path / "b.parquet")
    t = read_table([tmp_path / "a.parquet", tmp_path / "b.parquet"])
    assert t["opt"].to_pylist() == [None, 2.5]


def test_498_a_record_batch_reader_source_can_be_read_only_once():
    t = pa.table({"a": [1, 2, 3]})
    src = open_source(pa.RecordBatchReader.from_batches(t.schema, t.to_batches()))
    assert src.table().num_rows == 3
    with pytest.raises(ReaderError, match="only once"):
        src.table()
