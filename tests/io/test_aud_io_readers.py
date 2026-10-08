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


def test_499_unreadable_files_raise_reader_error(tmp_path):
    for name in ("junk.parquet", "junk.arrow"):
        p = tmp_path / name
        p.write_bytes(b"garbage")
        with pytest.raises(ReaderError, match=r"cannot read .*junk"):
            read_table(p)


def test_499_an_unknown_column_raises_reader_error_for_every_file_kind(tmp_path):
    t = pa.table({"a": [1, 2]})
    pq.write_table(t, tmp_path / "t.parquet")
    with pa.ipc.new_file(tmp_path / "t.arrow", t.schema) as w:
        w.write_table(t)
    _write(tmp_path / "t.csv", "a\n1\n2\n")
    _write(tmp_path / "t.jsonl", '{"a": 1}\n{"a": 2}\n')
    for name in ("t.parquet", "t.arrow", "t.csv", "t.jsonl"):
        with pytest.raises(ReaderError, match=r"columns not found: \['zz'\]"):
            read_table(tmp_path / name, columns=["a", "zz"])
        assert read_table(tmp_path / name, columns=["a"])["a"].to_pylist() == [1, 2]
    with pytest.raises(ReaderError, match="columns not found"):
        read_table(tmp_path / "t.csv", columns=["zz"], csv=CsvOptions(stream=True))


def test_506_a_file_source_is_named_without_its_format_and_compression_suffixes(tmp_path):
    names = {
        "my.data.csv": "my.data",
        "sales.2024-01.csv.gz": "sales.2024-01",
        "a.b.c.JSONL": "a.b.c",
        "orders.parquet": "orders",  # one dot: unchanged
        "plain.csv.gz": "plain",
    }
    for file_name, expected in names.items():
        path = tmp_path / file_name
        if file_name.endswith(".gz"):
            import gzip

            path.write_bytes(gzip.compress(b"k\n1\n"))
        elif file_name.endswith(".parquet"):
            pq.write_table(pa.table({"k": [1]}), path)
        elif file_name.lower().endswith(".jsonl"):
            _write(path, '{"k": 1}\n')
        else:
            _write(path, "k\n1\n")
        assert open_source(path).name == expected, file_name
    # two distinct files no longer collapse to one table name
    a = _write(tmp_path / "sales.2024-02.csv", "k\n1\n")
    b = _write(tmp_path / "sales.2024-03.csv", "k\n1\n")
    assert open_source(a).name != open_source(b).name
    # an explicit name still wins
    assert open_source(a, name="t").name == "t"
