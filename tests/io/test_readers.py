"""P1-04: readers turn every source into typed Arrow record batches."""

from __future__ import annotations

import datetime as dt
import gzip
import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.capture import capture_rows
from shape.io import (
    PANDAS_CSV,
    CsvOptions,
    ReaderError,
    expand_paths,
    iter_rows,
    open_source,
    read_batches,
    read_table,
)

CSV = (
    "id,price,active,signup,seen,zip,note\n"
    "1,9.5,true,2020-01-05,2020-01-05 10:00:00,02134,a\n"
    "2,10.25,false,2020-02-06,2020-02-06 11:30:00,10001,\n"
    "3,,true,2020-03-07,2020-03-07 12:45:00,94105,c\n"
)


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def _types(table: pa.Table) -> dict[str, str]:
    return {f.name: str(f.type) for f in table.schema}


# ------------------------------------------------------------------------ golden set


def test_csv_values_are_typed_not_strings(tmp_path):
    """P1: CSV values used to arrive as strings, so every column was `text`."""
    t = read_table(_write(tmp_path / "g.csv", CSV))
    assert _types(t) == {
        "id": "int64",
        "price": "double",
        "active": "bool",
        "signup": "date32[day]",
        "seen": "timestamp[s]",
        "zip": "string",  # leading zeros (02134): an identifier is text, not 2134 (issue #46)
        "note": "string",
    }
    assert t["price"].to_pylist() == [9.5, 10.25, None]
    assert t["active"].to_pylist() == [True, False, True]
    assert t["signup"][0].as_py() == dt.date(2020, 1, 5)
    assert t["note"].to_pylist() == ["a", None, "c"]


def test_csv_schema_override_keeps_leading_zeros(tmp_path):
    p = _write(tmp_path / "g.csv", CSV)
    t = read_table(p, csv=CsvOptions(column_types={"zip": "string", "id": pa.int32()}))
    assert t["zip"].to_pylist() == ["02134", "10001", "94105"]
    assert str(t.schema.field("id").type) == "int32"
    with pytest.raises(ReaderError, match="unknown Arrow type"):
        read_table(p, csv=CsvOptions(column_types={"zip": "not-a-type"}))


def test_csv_typed_rows_reach_capture_as_numbers(tmp_path):
    p = _write(tmp_path / "n.csv", "a,b\n1,x\n2,y\n3,z\n")
    rows = list(iter_rows(p))
    assert rows == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}]
    cols = capture_rows(iter(rows)).to_dict()
    text = json.dumps(cols, default=str)
    assert '"a"' in text and "numeric" in text.lower()


def test_pandas_token_preset_matches_read_csv_semantics(tmp_path):
    p = _write(tmp_path / "na.csv", "a,b,c\nNA,True,x\n1,false,None\n,TRUE,n/a\n")
    t = read_table(p, csv=PANDAS_CSV)
    expect = pd.read_csv(p)
    assert t["a"].to_pylist() == [None if pd.isna(v) else v for v in expect["a"]]
    assert t["c"].null_count == int(expect["c"].isna().sum()) == 2
    assert str(t.schema.field("b").type) == "bool"


def test_tsv_and_compressed_csv(tmp_path):
    tsv = _write(tmp_path / "x.tsv", "a\tb\n1\t2\n3\t4\n")
    assert read_table(tsv).to_pydict() == {"a": [1, 3], "b": [2, 4]}
    gz = tmp_path / "x.csv.gz"
    gz.write_bytes(gzip.compress(b"a,b\n1,2\n3,4\n"))
    assert read_table(gz).to_pydict() == {"a": [1, 3], "b": [2, 4]}


def test_csv_streaming_reader_gives_the_same_table(tmp_path):
    body = "a,b\n" + "".join(f"{i},{i * 0.5}\n" for i in range(5000))
    p = _write(tmp_path / "s.csv", body)
    whole = read_table(p)
    streamed = read_table(p, csv=CsvOptions(stream=True, block_size=4096))
    assert whole.equals(streamed)


def test_parquet_streams_by_row_group_and_projects_columns(tmp_path):
    table = pa.table(
        {"i": pa.array(range(1000)), "s": [f"v{i}" for i in range(1000)], "f": [0.5] * 1000}
    )
    p = tmp_path / "g.parquet"
    pq.write_table(table, p, row_group_size=250)
    src = open_source(p, batch_size=100)
    assert src.num_rows == 1000 and src.schema.equals(table.schema)
    sizes = [b.num_rows for b in src.batches()]
    assert sum(sizes) == 1000 and max(sizes) <= 100 and len(sizes) >= 10
    proj = read_table(p, columns=["s", "i"])
    assert proj.column_names == ["s", "i"] and proj.num_rows == 1000
    assert read_table(p).equals(table)


def test_jsonl_types_and_explicit_schema(tmp_path):
    p = _write(
        tmp_path / "g.jsonl",
        '{"a": 1, "b": "x", "c": 1.5}\n{"a": 2, "b": null, "c": 2}\n'
        '{"a": 3, "b": "z", "c": null}\n',
    )
    t = read_table(p)
    assert _types(t) == {"a": "int64", "b": "string", "c": "double"}
    forced = read_table(
        p, schema=pa.schema([("a", pa.int32()), ("b", pa.string()), ("c", pa.float32())])
    )
    assert str(forced.schema.field("a").type) == "int32"
    with pytest.raises(ReaderError, match="JSON lines"):
        read_table(_write(tmp_path / "bad.jsonl", '{"a": 1}\nnot json\n'))


def test_arrow_ipc_file_and_stream(tmp_path):
    table = pa.table({"a": [1, 2, 3], "b": ["x", "y", None]})
    f = tmp_path / "f.arrow"
    with pa.ipc.new_file(str(f), table.schema) as w:
        w.write_table(table)
    s = tmp_path / "s.ipc"
    with pa.ipc.new_stream(str(s), table.schema) as w:
        w.write_table(table)
    assert read_table(f).equals(table)
    assert read_table(s).equals(table)
    assert open_source(f).schema.equals(table.schema)


def test_dict_of_arrays_is_zero_copy_for_numpy():
    arr = np.arange(1_000_000, dtype=np.int64)
    t = read_table({"a": arr})
    assert t["a"].chunks[0].buffers()[1].address == arr.__array_interface__["data"][0]
    assert read_table({"a": pa.array([1, 2]), "b": ["x", "y"]}).num_rows == 2
    with pytest.raises(ReaderError):
        read_table({"a": [1, 2, 3], "b": [1]})


def test_pandas_and_arrow_capsule_objects(tmp_path):
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", None, "z"], "c": [0.5, 1.5, 2.5]})
    t = read_table(df)
    assert t["a"].to_pylist() == [1, 2, 3] and t["b"].to_pylist() == ["x", None, "z"]
    assert len(df.index) == 3 and t.column_names == ["a", "b", "c"]  # the index is not a column

    class PolarsLike:  # polars.DataFrame exports the Arrow PyCapsule stream like this
        def __init__(self, table):
            self._t = table

        def __arrow_c_stream__(self, requested_schema=None):
            return self._t.__arrow_c_stream__(requested_schema)

    base = pa.table({"a": np.arange(1000, dtype=np.int64)})
    got = read_table(PolarsLike(base))
    assert got["a"].chunks[0].buffers()[1].address == base["a"].chunks[0].buffers()[1].address


def test_arrow_tables_batches_and_readers():
    t = pa.table({"a": [1, 2, 3, 4, 5]})
    assert [b.num_rows for b in read_batches(t, batch_size=2)] == [2, 2, 1]
    assert read_table(t.to_batches()[0]).equals(t)
    reader = pa.RecordBatchReader.from_batches(t.schema, t.to_batches())
    assert read_table(reader).equals(t)
    assert read_table(t, columns=["a"]).equals(t)
    with pytest.raises(ReaderError, match="not found"):
        read_table(t, columns=["zzz"])


def test_row_iterables_edge_adapter():
    rows = [{"a": 1, "b": "x"}, {"a": 2}, {"a": None, "b": "z", "c": 1.5}]
    t = read_table(iter(rows))
    assert t.column_names == ["a", "b", "c"]
    assert t["a"].to_pylist() == [1, 2, None]
    # leading None does not lock the column as text (P3 at the reader level)
    t = read_table([{"x": None}, {"x": 1}, {"x": 2}])
    assert str(t.schema.field("x").type) == "int64" and t["x"].to_pylist() == [None, 1, 2]
    # numpy scalars and Decimal keep a numeric type (P4 at the reader level)
    t = read_table(
        [{"n": np.int64(3), "d": Decimal("1.50")}, {"n": np.int64(4), "d": Decimal("2.5")}]
    )
    assert str(t.schema.field("n").type) == "int64" and pa.types.is_decimal(
        t.schema.field("d").type
    )


def test_row_iterable_mixed_types_keep_every_value():
    t = read_table([{"v": 1}, {"v": 2}, {"v": "x"}, {"v": 3}])
    assert t["v"].to_pylist() == ["1", "2", "x", "3"]
    # a later batch that disagrees with the first is kept as text instead of failing
    out = read_table(({"v": v} for v in [1, 2, 3, "x", 5]), batch_size=3)
    assert out["v"].to_pylist() == ["1", "2", "3", "x", "5"]
    assert list(iter_rows([{"a": 1}])) == [{"a": 1}]
    src = open_source(iter([{"a": 1}]))
    list(src.batches())
    with pytest.raises(ReaderError, match="only once"):
        list(src.batches())


# --------------------------------------------------------------------- globs, dirs, multi-file


def _make_tree(tmp_path):
    (tmp_path / "d" / "sub").mkdir(parents=True)
    _write(tmp_path / "d" / "a.csv", "k,v\n1,x\n2,y\n")
    _write(tmp_path / "d" / "sub" / "b.csv", "k,v\n3,z\n")
    _write(tmp_path / "d" / ".hidden.csv", "k,v\n9,h\n")
    _write(tmp_path / "d" / "_tmp.csv", "k,v\n9,h\n")
    _write(tmp_path / "d" / "readme.txt", "ignore me")
    return tmp_path / "d"


def test_glob_and_directory_expansion(tmp_path):
    d = _make_tree(tmp_path)
    assert [p.name for p in expand_paths(d)] == ["a.csv", "b.csv"]
    assert [p.name for p in expand_paths(str(d / "*.csv"))] == ["_tmp.csv", "a.csv"]
    assert [p.name for p in expand_paths(str(d / "**" / "*.csv"))] == ["_tmp.csv", "a.csv", "b.csv"]
    assert [p.name for p in expand_paths([d / "a.csv", d / "a.csv"])] == ["a.csv"]
    t = read_table(d)
    assert t["k"].to_pylist() == [1, 2, 3] and open_source(d).name == "d"
    with pytest.raises(FileNotFoundError, match="no files match"):
        expand_paths(str(d / "*.nothing"))
    with pytest.raises(FileNotFoundError):
        expand_paths(tmp_path / "missing.csv")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ReaderError, match="holds no"):
        expand_paths(tmp_path / "empty")


def test_multi_file_schema_follows_the_first_file(tmp_path):
    a = _write(tmp_path / "a.csv", "k,v\n1,1.5\n2,2.5\n")
    b = _write(tmp_path / "b.csv", "v,k\n3,3\n")  # same columns, other order, k/v ints
    t = read_table([a, b])
    assert t.column_names == ["k", "v"] and t["v"].to_pylist() == [1.5, 2.5, 3.0]
    c = _write(tmp_path / "c.csv", "k,other\n1,2\n")
    with pytest.raises(ReaderError, match="different columns"):
        read_table([a, c])


def test_errors(tmp_path):
    with pytest.raises(ReaderError, match="unsupported file type"):
        read_table(_write(tmp_path / "x.docx", "junk"))
    a = _write(tmp_path / "a.csv", "k\n1\n")
    b = _write(tmp_path / "b.jsonl", '{"k": 1}\n')
    with pytest.raises(ReaderError, match="mixed types"):
        read_table([a, b])
    with pytest.raises(ReaderError, match="unsupported source type"):
        open_source(12345)
    with pytest.raises(ValueError, match="batch_size"):
        open_source(a, batch_size=0)


def test_batch_sizes_and_source_metadata(tmp_path):
    p = _write(tmp_path / "many.csv", "a\n" + "".join(f"{i}\n" for i in range(10)))
    src = open_source(p, batch_size=4)
    assert src.kind == "csv" and src.name == "many" and src.num_rows == 10
    assert [b.num_rows for b in src.batches()] == [4, 4, 2]
    assert src.table().num_rows == 10
